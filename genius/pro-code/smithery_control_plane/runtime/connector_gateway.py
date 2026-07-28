"""Mandatory AKOS execution gateway for connector entrypoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import inspect
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, TypeVar

from .connector_policy import (
    AttestationKind,
    AttestationOutcome,
    ConnectorPolicy,
    JsonlRepairSink,
    Operation,
    OperationRequest,
    PolicyError,
    RouteObservation,
    AuditReceipt,
    CompletionRejected,
    RouteDecision,
    new_request,
    sign_attestation,
    sanitize_audit_metadata,
)


T = TypeVar("T")


@dataclass(frozen=True)
class ProbeEvidence:
    passed: bool
    authenticated_tenant_alias: str
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutcome(Generic[T]):
    result: T
    completion_proofs: frozenset[str]
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    artifact_bytes: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPolicySpec:
    service: str
    route: str
    operation: Operation
    capability: str
    static_proofs: frozenset[str]
    completion_capabilities: frozenset[str]


_READ_COMPLETION = frozenset({"bounded_read", "validated_response"})
_COMPUTE_COMPLETION = frozenset({"validated_response"})
_WRITE_COMPLETION = frozenset(
    {
        "scoped_write",
        "matching_readback",
        "cleanup_or_approved_retention",
        "artifact_hash",
        "registry_update",
    }
)
_LOCAL_RECEIPT = frozenset({"matching_local_and_cloud_receipts"})
_MAX_RESULT_BYTES = 1024 * 1024
_ERROR_STATUS_WORDS = frozenset(
    {"error", "failed", "failure", "unauthorized", "forbidden"}
)


def _external_read_spec() -> ToolPolicySpec:
    return ToolPolicySpec(
        service="fileboss_mcp_tools",
        route="FILEBOSS Remote MCP",
        operation=Operation.READ,
        capability="read",
        static_proofs=frozenset({"current_schema"}),
        completion_capabilities=_READ_COMPLETION,
    )


def _compute_spec() -> ToolPolicySpec:
    return ToolPolicySpec(
        service="fileboss_mcp_tools",
        route="FILEBOSS Remote MCP",
        operation=Operation.COMPUTE,
        capability="compute",
        static_proofs=frozenset({"current_schema"}),
        completion_capabilities=_COMPUTE_COMPLETION,
    )


def _unproven_external_write_spec() -> ToolPolicySpec:
    # The route is intentionally unable to satisfy the completion contract yet.
    # Authorization therefore fails before any external mutation occurs.
    return ToolPolicySpec(
        service="fileboss_mcp_tools",
        route="FILEBOSS Remote MCP",
        operation=Operation.WRITE,
        capability="write",
        static_proofs=frozenset({"current_schema", "isolated_target"}),
        completion_capabilities=frozenset(),
    )


def _local_spec(operation: Operation) -> ToolPolicySpec:
    return ToolPolicySpec(
        service="desktop_and_local_files",
        route="FILEBOSS Remote MCP Local Organizer",
        operation=operation,
        capability=operation.value,
        static_proofs=frozenset({"current_schema", "policy_scope"}),
        # The current local organizer has not yet proven a matching cloud
        # receipt. Omitting that capability makes authorization fail before
        # disk I/O instead of executing and rejecting afterward.
        completion_capabilities=(
            _READ_COMPLETION
            if operation is Operation.LOCAL_READ
            else _WRITE_COMPLETION
        ),
    )


def resolve_mcp_tool_spec(name: str, arguments: Mapping[str, Any]) -> ToolPolicySpec:
    """Translate a tool request to a closed operation class.

    Caller-provided fields named ``operation``, ``temporary``, ``local``, or
    similar are ignored. The trusted registry below controls classification.
    """

    if name in {"fileboss_search", "fileboss_legal_search", "fileboss_memory_recall"}:
        return _external_read_spec()
    if name == "fileboss_motion_draft":
        return _compute_spec()
    if name == "fileboss_memory_store":
        return _unproven_external_write_spec()
    if name == "fileboss_case_evidence":
        action = str(arguments.get("action", ""))
        if action in {"get", "list"}:
            return _external_read_spec()
        if action in {"add", "tag"}:
            return _unproven_external_write_spec()
        raise PolicyError(f"Unsupported evidence action: {action}")
    if name == "fileboss_dropbox_sync":
        action = str(arguments.get("action", ""))
        if action in {"list", "get"}:
            return _external_read_spec()
        if action in {"sync", "upload"}:
            return _unproven_external_write_spec()
        raise PolicyError(f"Unsupported Dropbox action: {action}")
    if name == "fileboss_local_analyze":
        return _local_spec(Operation.LOCAL_READ)
    if name in {"fileboss_local_index", "fileboss_local_organize", "fileboss_local_undo"}:
        return _local_spec(Operation.LOCAL_WRITE)
    raise PolicyError(f"Tool lacks a policy classification: {name}")


def resolve_actor_operation_spec(name: str) -> ToolPolicySpec:
    if name in {"health", "recall"}:
        return ToolPolicySpec(
            service="actor_violations_pipeline",
            route="APEX Actor Violations Connector",
            operation=Operation.READ,
            capability="read",
            static_proofs=frozenset({"current_schema"}),
            completion_capabilities=_READ_COMPLETION,
        )
    if name == "ingest":
        return ToolPolicySpec(
            service="actor_violations_pipeline",
            route="APEX Actor Violations Connector",
            operation=Operation.WRITE,
            capability="write",
            static_proofs=frozenset({"current_schema", "isolated_target"}),
            completion_capabilities=frozenset(),
        )
    raise PolicyError(f"Actor connector operation lacks classification: {name}")


class PolicyGateway:
    """Issue current-request attestations and wrap connector execution."""

    def __init__(
        self,
        *,
        policy: ConnectorPolicy,
        attestation_key: bytes,
        tenant_alias: str,
        allowed_local_roots: tuple[Path, ...] = (),
    ) -> None:
        if not attestation_key:
            raise PolicyError("AKOS_ATTESTATION_HMAC_KEY is required")
        if not tenant_alias:
            raise PolicyError("AKOS_TENANT_ALIAS is required")
        self.policy = policy
        self._attestation_key = bytes(attestation_key)
        self.tenant_alias = tenant_alias
        self.allowed_local_roots = tuple(path.resolve() for path in allowed_local_roots)

    @classmethod
    def from_environment(
        cls,
        policy_path: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> "PolicyGateway":
        env = dict(os.environ if environment is None else environment)
        trusted_digest = env.get("AKOS_POLICY_SHA256", "")
        attestation_secret = env.get("AKOS_ATTESTATION_HMAC_KEY", "")
        tenant_alias = env.get("AKOS_TENANT_ALIAS", "")
        repair_queue = env.get(
            "AKOS_REPAIR_QUEUE",
            "genius/pro-code/smithery_control_plane/audit/repair_queue.jsonl",
        )
        allowed_roots = tuple(
            Path(item).expanduser()
            for item in env.get("FILEBOSS_ALLOWED_ROOTS", "").split(os.pathsep)
            if item.strip()
        )
        if not trusted_digest:
            raise PolicyError("AKOS_POLICY_SHA256 is required")
        if not attestation_secret:
            raise PolicyError("AKOS_ATTESTATION_HMAC_KEY is required")
        if not tenant_alias:
            raise PolicyError("AKOS_TENANT_ALIAS is required")
        policy = ConnectorPolicy.load(
            policy_path,
            trusted_policy_sha256=trusted_digest,
            attestation_key=attestation_secret.encode("utf-8"),
            repair_sink=JsonlRepairSink(repair_queue),
        )
        return cls(
            policy=policy,
            attestation_key=attestation_secret.encode("utf-8"),
            tenant_alias=tenant_alias,
            allowed_local_roots=allowed_roots,
        )

    def _local_preflight_proofs(
        self,
        spec: ToolPolicySpec,
        arguments: Mapping[str, Any],
    ) -> frozenset[str]:
        if spec.operation not in {Operation.LOCAL_READ, Operation.LOCAL_WRITE}:
            return frozenset()
        path_value = arguments.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise PolicyError("Local operations require a path")
        target = Path(path_value).expanduser().resolve()
        approved = any(target == root or root in target.parents for root in self.allowed_local_roots)
        if not approved:
            raise PolicyError("Local path is outside FILEBOSS_ALLOWED_ROOTS")
        return frozenset(
            {"device_online", "same_run_ping", "approved_root", "policy_scope"}
        )

    async def _runtime_probe_evidence(
        self,
        probe: Callable[[], Awaitable[ProbeEvidence] | ProbeEvidence],
    ) -> ProbeEvidence:
        try:
            result = probe()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return ProbeEvidence(False, "", {"reason": "probe_exception"})
        if not isinstance(result, ProbeEvidence):
            return ProbeEvidence(False, "", {"reason": "untrusted_probe_shape"})
        alias = result.authenticated_tenant_alias.strip()
        if result.passed and not alias:
            return ProbeEvidence(False, "", {"reason": "authenticated_identity_missing"})
        return ProbeEvidence(bool(result.passed), alias, dict(result.details))

    def build_observation(
        self,
        *,
        request: OperationRequest,
        spec: ToolPolicySpec,
        arguments: Mapping[str, Any],
        probe_evidence: ProbeEvidence,
    ) -> RouteObservation:
        authenticated = probe_evidence.authenticated_tenant_alias
        identity_passed = bool(probe_evidence.passed and authenticated)
        affinity_passed = bool(
            identity_passed and authenticated == request.requested_tenant_alias
        )
        identity = sign_attestation(
            key=self._attestation_key,
            kind=AttestationKind.IDENTITY,
            request=request,
            route=spec.route,
            outcome=(
                AttestationOutcome.PASSED
                if identity_passed
                else AttestationOutcome.FAILED
            ),
            claims={"authenticated_tenant_alias": authenticated},
        )
        affinity = sign_attestation(
            key=self._attestation_key,
            kind=AttestationKind.TENANT_AFFINITY,
            request=request,
            route=spec.route,
            outcome=(
                AttestationOutcome.PASSED
                if affinity_passed
                else AttestationOutcome.FAILED
            ),
            claims={
                "requested_tenant_alias": request.requested_tenant_alias,
                "authenticated_tenant_alias": authenticated,
            },
        )
        runtime = sign_attestation(
            key=self._attestation_key,
            kind=AttestationKind.RUNTIME_PROBE,
            request=request,
            route=spec.route,
            outcome=(
                AttestationOutcome.PASSED
                if probe_evidence.passed
                else AttestationOutcome.FAILED
            ),
            claims={"probe_scope": f"{request.service}:{request.operation.value}"},
        )
        proofs = set(spec.static_proofs)
        proofs.update(self._local_preflight_proofs(spec, arguments))
        return RouteObservation(
            route=spec.route,
            verification="Verified",
            capabilities=frozenset({spec.capability}),
            proofs=frozenset(proofs),
            completion_capabilities=spec.completion_capabilities,
            attestations=(identity, affinity, runtime),
        )

    async def execute(
        self,
        *,
        spec: ToolPolicySpec,
        arguments: Mapping[str, Any],
        target_alias: str,
        probe: Callable[[], Awaitable[ProbeEvidence] | ProbeEvidence],
        callback: Callable[[], Awaitable[ExecutionOutcome[T]] | ExecutionOutcome[T]],
    ) -> tuple[T, AuditReceipt]:
        request = new_request(
            service=spec.service,
            operation=spec.operation,
            requested_tenant_alias=self.tenant_alias,
            target_alias=target_alias,
        )
        probe_evidence = await self._runtime_probe_evidence(probe)
        observation = self.build_observation(
            request=request,
            spec=spec,
            arguments=arguments,
            probe_evidence=probe_evidence,
        )
        decision = self.policy.authorize(request, [observation])
        try:
            outcome = callback()
            if inspect.isawaitable(outcome):
                outcome = await outcome
            if not isinstance(outcome, ExecutionOutcome):
                raise PolicyError(
                    "Connector callback must return ExecutionOutcome with completion proofs"
                )
        except Exception as exc:
            receipt = self.policy.build_rejected_receipt(
                decision,
                ("connector_runtime_failure",),
            )
            repair = None
            try:
                receipt, repair = self.policy.record_failure(
                    decision,
                    ("connector_runtime_failure",),
                )
            except Exception as persistence_exc:
                try:
                    setattr(
                        exc,
                        "akos_repair_persistence_error",
                        sanitize_audit_metadata(str(persistence_exc)),
                    )
                except Exception:
                    pass
            for name, value in (
                ("akos_rejected_receipt", receipt),
                ("akos_repair_instruction", repair),
            ):
                if value is None:
                    continue
                try:
                    setattr(exc, name, value)
                except Exception:
                    pass
            raise

        receipt = self.policy.complete(
            decision,
            completion_proofs=outcome.completion_proofs,
            artifact_hashes=outcome.artifact_hashes,
            artifact_bytes=outcome.artifact_bytes,
        )
        return outcome.result, receipt


def _serialized_result(result: Any) -> tuple[bytes, str]:
    try:
        serialized = json.dumps(
            result,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PolicyError("Connector result is not safely serializable") from exc
    if len(serialized) > _MAX_RESULT_BYTES:
        raise PolicyError(
            f"Connector result exceeds bounded response limit of {_MAX_RESULT_BYTES} bytes"
        )
    return serialized, sha256(serialized).hexdigest()


def _error_value_present(value: Any) -> bool:
    return value not in (None, False, "", (), [], {})


def _validate_result_tree(result: Any) -> None:
    stack: list[tuple[str, Any, int]] = [("$", result, 0)]
    nodes = 0
    while stack:
        path, value, depth = stack.pop()
        nodes += 1
        if nodes > 10000 or depth > 32:
            raise PolicyError("Connector result exceeds validation complexity limits")
        if isinstance(value, Mapping):
            if bool(value.get("isError")):
                raise PolicyError(f"Connector result reports an execution error at {path}")
            if _error_value_present(value.get("error")) or _error_value_present(
                value.get("errors")
            ):
                raise PolicyError(f"Connector result contains an error payload at {path}")
            for key in ("status_code", "statusCode", "http_status"):
                status = value.get(key)
                if isinstance(status, bool):
                    continue
                try:
                    numeric_status = int(status)
                except (TypeError, ValueError):
                    continue
                if numeric_status >= 400:
                    raise PolicyError(
                        f"Connector result reports an unsuccessful status at {path}"
                    )
            status = value.get("status")
            if isinstance(status, str) and status.strip().lower() in _ERROR_STATUS_WORDS:
                raise PolicyError(f"Connector result reports a failed status at {path}")
            for key, child in value.items():
                stack.append((f"{path}.{key}", child, depth + 1))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                stack.append((f"{path}[{index}]", child, depth + 1))


def validate_result(result: T) -> tuple[T, str]:
    if result is None:
        raise PolicyError("Connector result is empty")
    _validate_result_tree(result)
    _, digest = _serialized_result(result)
    return result, digest


def read_outcome(result: T) -> ExecutionOutcome[T]:
    validated, digest = validate_result(result)
    return ExecutionOutcome(
        result=validated,
        completion_proofs=_READ_COMPLETION,
        artifact_hashes={"response_sha256": digest},
    )


def compute_outcome(result: T) -> ExecutionOutcome[T]:
    validated, digest = validate_result(result)
    return ExecutionOutcome(
        result=validated,
        completion_proofs=_COMPUTE_COMPLETION,
        artifact_hashes={"response_sha256": digest},
    )


def result_sha256(result: Any) -> str:
    _, digest = _serialized_result(result)
    return digest


@lru_cache(maxsize=1)
def default_gateway() -> PolicyGateway:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "akos_connector_policy.json"
    )
    return PolicyGateway.from_environment(policy_path)


async def execute_mcp_tool(
    name: str,
    arguments: Mapping[str, Any],
    probe: Callable[[], Awaitable[ProbeEvidence] | ProbeEvidence],
    callback: Callable[[], Awaitable[ExecutionOutcome[T]] | ExecutionOutcome[T]],
) -> tuple[T, AuditReceipt]:
    spec = resolve_mcp_tool_spec(name, arguments)
    target_alias = f"mcp:{name}"
    return await default_gateway().execute(
        spec=spec,
        arguments=arguments,
        target_alias=target_alias,
        probe=probe,
        callback=callback,
    )


async def execute_actor_operation(
    name: str,
    arguments: Mapping[str, Any],
    probe: Callable[[], Awaitable[ProbeEvidence] | ProbeEvidence],
    callback: Callable[[], Awaitable[ExecutionOutcome[T]] | ExecutionOutcome[T]],
) -> tuple[T, AuditReceipt]:
    spec = resolve_actor_operation_spec(name)
    return await default_gateway().execute(
        spec=spec,
        arguments=arguments,
        target_alias=f"actor_violations:{name}",
        probe=probe,
        callback=callback,
    )
