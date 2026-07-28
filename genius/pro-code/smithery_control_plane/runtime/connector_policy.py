"""AKOS connector policy enforcement.

This module is intentionally fail-closed. It separates:
- immutable policy and route authority loading,
- signed current-run identity / affinity / runtime attestations,
- pre-execution authorization,
- post-execution completion verification,
- sanitized audit receipts, and
- durable repair instructions.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import hmac
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback below
    fcntl = None


class PolicyError(ValueError):
    """Raised when policy, authority, proof, or routing violates control rules."""


class Operation(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    TEMPORARY_PROBE = "temporary_probe"
    COMPUTE = "compute"
    LOCAL_READ = "local_read"
    LOCAL_WRITE = "local_write"


class AttestationKind(str, Enum):
    IDENTITY = "identity"
    TENANT_AFFINITY = "tenant_affinity"
    RUNTIME_PROBE = "runtime_probe"


class AttestationOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class OperationRequest:
    request_id: str
    service: str
    operation: Operation
    requested_tenant_alias: str
    target_alias: str
    issued_at: str
    expires_at: str


@dataclass(frozen=True)
class Attestation:
    kind: AttestationKind
    request_id: str
    service: str
    route: str
    operation: Operation
    outcome: AttestationOutcome
    issued_at: str
    expires_at: str
    nonce: str
    claims: tuple[tuple[str, str], ...]
    signature: str

    def claim_map(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.claims))


@dataclass(frozen=True)
class RouteObservation:
    route: str
    verification: str
    capabilities: frozenset[str]
    proofs: frozenset[str]
    completion_capabilities: frozenset[str]
    attestations: tuple[Attestation, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    request: OperationRequest
    selected_route: str
    selected_role: str
    verification: str
    authorization_proofs: frozenset[str]
    required_completion_gates: frozenset[str]
    rejected_routes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairInstruction:
    instruction_id: str
    request_id: str
    service: str
    operation: str
    created_at: str
    failures: Mapping[str, tuple[str, ...]]
    action: str = "repair_and_retry"


@dataclass(frozen=True)
class AuditReceipt:
    receipt_id: str
    timestamp: str
    request_id: str
    service: str
    route: str
    operation: str
    decision: str
    verification: str
    authorization_proofs: tuple[str, ...]
    completion_proofs: tuple[str, ...]
    artifact_hashes: Mapping[str, str]
    failed_gates: tuple[str, ...]


class RepairSink(Protocol):
    def enqueue(self, instruction: RepairInstruction) -> None:
        """Persist a sanitized repair instruction."""


class NoApprovedRoute(PolicyError):
    """Raised when no authority-approved route satisfies current preflight gates."""

    def __init__(
        self,
        request: OperationRequest,
        failures: Mapping[str, Sequence[str]],
        repair_instruction: RepairInstruction,
    ) -> None:
        self.request = request
        self.failures = {name: tuple(gates) for name, gates in failures.items()}
        self.repair_instruction = repair_instruction
        super().__init__(
            f"No approved route for {request.service}/{request.operation.value}: "
            f"{self.failures}"
        )


class CompletionRejected(PolicyError):
    """Raised when execution completed but required postflight proofs are missing."""

    def __init__(self, receipt: AuditReceipt, repair_instruction: RepairInstruction) -> None:
        self.receipt = receipt
        self.repair_instruction = repair_instruction
        super().__init__(
            f"Completion rejected for {receipt.service}/{receipt.operation}: "
            f"{receipt.failed_gates}"
        )


@dataclass
class InMemoryRepairSink:
    instructions: list[RepairInstruction] = field(default_factory=list)

    def enqueue(self, instruction: RepairInstruction) -> None:
        self.instructions.append(instruction)


class JsonlRepairSink:
    """Concurrent append-only hash chain with incremental tail verification."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._verified_signature: tuple[int, int] | None = None
        self._verified_size = 0
        self._tail_hash = "0" * 64

    @staticmethod
    def _record_digest(record: Mapping[str, Any]) -> str:
        unsigned = {key: value for key, value in record.items() if key != "record_sha256"}
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PolicyError(f"Cannot stat repair queue {self.path}: {exc}") from exc
        return stat.st_size, stat.st_mtime_ns

    @contextmanager
    def _exclusive_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            with self._lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return

        deadline = time.monotonic() + 10.0
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    self._lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise PolicyError(f"Timed out acquiring repair queue lock {self._lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass

    def _verify_lines(
        self,
        lines: Sequence[str],
        *,
        previous: str,
        start_index: int,
    ) -> str:
        current = previous
        for index, line in enumerate(lines, start=start_index):
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PolicyError(f"Repair queue line {index} is invalid JSON") from exc
            if not isinstance(record, Mapping):
                raise PolicyError(f"Repair queue line {index} is not a JSON object")
            if record.get("previous_sha256") != current:
                raise PolicyError(f"Repair queue chain breaks at line {index}")
            stored = record.get("record_sha256")
            if not isinstance(stored, str) or not _SHA256_RE.fullmatch(stored):
                raise PolicyError(f"Repair queue line {index} has invalid SHA-256")
            if not hmac.compare_digest(stored.lower(), self._record_digest(record)):
                raise PolicyError(f"Repair queue digest mismatch at line {index}")
            current = stored.lower()
        return current

    def verify_chain(self, *, force: bool = False) -> bool:
        signature = self._signature()
        if signature is None:
            self._verified_signature = None
            self._verified_size = 0
            self._tail_hash = "0" * 64
            return True
        if not force and signature == self._verified_signature:
            return True

        size, mtime_ns = signature
        if (
            not force
            and self._verified_signature is not None
            and size > self._verified_size > 0
        ):
            try:
                with self.path.open("rb") as handle:
                    handle.seek(self._verified_size)
                    appended = handle.read()
            except OSError as exc:
                raise PolicyError(f"Cannot incrementally read repair queue {self.path}: {exc}") from exc
            try:
                text = appended.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PolicyError(f"Repair queue append is not UTF-8: {self.path}") from exc
            if text and not text.endswith("\n"):
                raise PolicyError("Repair queue has an incomplete appended record")
            previous_lines = 1
            try:
                with self.path.open("rb") as handle:
                    previous_lines += handle.read(self._verified_size).count(b"\n")
            except OSError as exc:
                raise PolicyError(f"Cannot count repair queue records {self.path}: {exc}") from exc
            self._tail_hash = self._verify_lines(
                text.splitlines(),
                previous=self._tail_hash,
                start_index=previous_lines,
            )
            self._verified_size = size
            self._verified_signature = (size, mtime_ns)
            return True

        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyError(f"Cannot read repair queue {self.path}: {exc}") from exc
        if text and not text.endswith("\n"):
            raise PolicyError("Repair queue has an incomplete final record")
        self._tail_hash = self._verify_lines(
            text.splitlines(),
            previous="0" * 64,
            start_index=1,
        )
        self._verified_size = size
        self._verified_signature = (size, mtime_ns)
        return True

    def enqueue(self, instruction: RepairInstruction) -> None:
        payload = sanitize_audit_metadata(
            {
                "instruction_id": instruction.instruction_id,
                "request_id": instruction.request_id,
                "service": instruction.service,
                "operation": instruction.operation,
                "created_at": instruction.created_at,
                "failures": dict(instruction.failures),
                "action": instruction.action,
            }
        )
        with self._exclusive_lock():
            self.verify_chain()
            record: dict[str, Any] = {
                "previous_sha256": self._tail_hash,
                "instruction": payload,
            }
            record["record_sha256"] = self._record_digest(record)
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise PolicyError(f"Cannot append repair queue {self.path}: {exc}") from exc
            self._tail_hash = str(record["record_sha256"])
            signature = self._signature()
            if signature is None:
                raise PolicyError("Repair queue disappeared after append")
            self._verified_size = signature[0]
            self._verified_signature = signature


# ---------------------------------------------------------------------------
# Audit sanitization
# ---------------------------------------------------------------------------

_EXACT_SENSITIVE_KEYS = {
    "password",
    "secret",
    "credential",
    "token",
    "authorization",
    "setup_url",
    "email",
    "account_id",
    "user_id",
    "session_id",
    "device_id",
    "evidence_content",
    "access_token",
    "refresh_token",
    "api_key",
    "private_key",
}
_SENSITIVE_SUFFIXES = (
    "_password",
    "_secret",
    "_credential",
    "_token",
    "_email",
    "_account_id",
    "_user_id",
    "_session_id",
    "_device_id",
    "_api_key",
    "_private_key",
)
_SAFE_INTERNAL_ID_KEYS = {
    "receipt_id",
    "instruction_id",
    "request_id",
    "event_id",
    "snapshot_id",
    "deployment_id",
    "policy_id",
}
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(\bbearer\s+|"
    r"\b(?:token|secret|password|credential|api[_ -]?key)\s*[:=]\s*)"
    r"(\S+)"
)
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{24,})(?![A-Za-z0-9_-])")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_RAW_ID_PREFIXES = ("id:", "mem-", "collection://")
_SENSITIVE_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "identityuserid",
    "identity_user_id",
    "user_id",
    "session_id",
    "device_id",
    "secret",
    "password",
    "credential",
    "api_key",
}


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SAFE_INTERNAL_ID_KEYS:
        return False
    if normalized in _EXACT_SENSITIVE_KEYS:
        return True
    return any(normalized.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def find_sensitive_policy_fields(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _is_sensitive_key(str(key)):
                findings.append(child_path)
            findings.extend(find_sensitive_policy_fields(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            findings.extend(find_sensitive_policy_fields(child, f"{path}[{index}]"))
    return findings


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_like_high_entropy_identifier(value: str) -> bool:
    if value.startswith(_RAW_ID_PREFIXES):
        return True
    if len(value) < 24 or value.isspace() or _HEX_DIGEST_RE.fullmatch(value):
        return False
    classes = sum(
        (
            any(character.islower() for character in value),
            any(character.isupper() for character in value),
            any(character.isdigit() for character in value),
            "_" in value or "-" in value,
        )
    )
    return classes >= 3 and _entropy(value) >= 3.5


def _redact_sensitive_url(match: re.Match[str]) -> str:
    value = match.group(0)
    parsed = urlsplit(value)
    query_keys = {name.lower() for name, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if "/setup" in parsed.path.lower() or query_keys.intersection(_SENSITIVE_QUERY_KEYS):
        return "[REDACTED_SETUP_URL]"
    return value


def _redact_generic_token(match: re.Match[str]) -> str:
    token = match.group(1)
    return "[REDACTED_IDENTIFIER]" if _looks_like_high_entropy_identifier(token) else token


def _sanitize_scalar(value: Any, *, key: str | None = None) -> Any:
    if not isinstance(value, str):
        return value
    if key is not None and _normalize_key(key) in _SAFE_INTERNAL_ID_KEYS:
        return value
    if key and ("sha" in key.lower() or "hash" in key.lower()) and _HEX_DIGEST_RE.fullmatch(value):
        return value

    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    redacted = _UUID_RE.sub("[REDACTED_IDENTIFIER]", redacted)
    redacted = _JWT_RE.sub("[REDACTED_CREDENTIAL]", redacted)
    redacted = _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED_CREDENTIAL]",
        redacted,
    )
    redacted = _URL_RE.sub(_redact_sensitive_url, redacted)
    redacted = _TOKEN_RE.sub(_redact_generic_token, redacted)
    return redacted


def sanitize_audit_metadata(value: Any, *, _key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_audit_metadata(child, _key=str(key))
            for key, child in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_metadata(child, _key=_key) for child in value]
    return _sanitize_scalar(value, key=_key)


# ---------------------------------------------------------------------------
# File and immutable-document helpers
# ---------------------------------------------------------------------------


def file_sha256(path: str | Path) -> str:
    path_obj = Path(path)
    if not path_obj.exists():
        raise PolicyError(f"File not found: {path_obj}")
    if not path_obj.is_file():
        raise PolicyError(f"Path is not a file: {path_obj}")
    digest = sha256()
    try:
        with path_obj.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise PolicyError(f"Cannot read file {path_obj}: {exc}") from exc
    return digest.hexdigest()


def _sidecar_digest(sidecar: str | Path) -> str:
    sidecar_obj = Path(sidecar)
    if not sidecar_obj.exists():
        raise PolicyError(f"Sidecar file not found: {sidecar_obj}")
    if not sidecar_obj.is_file():
        raise PolicyError(f"Sidecar path is not a file: {sidecar_obj}")
    try:
        parts = sidecar_obj.read_text(encoding="utf-8").split()
    except OSError as exc:
        raise PolicyError(f"Cannot read sidecar {sidecar_obj}: {exc}") from exc
    if not parts or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
        raise PolicyError(f"Malformed SHA-256 sidecar: {sidecar_obj}")
    return parts[0].lower()


def verify_sha256_sidecar(path: str | Path, sidecar: str | Path) -> bool:
    return hmac.compare_digest(file_sha256(path), _sidecar_digest(sidecar))


def _load_json(path: Path, label: str) -> MutableMapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError as exc:
        raise PolicyError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{label} JSON is invalid: {path}: {exc}") from exc
    except OSError as exc:
        raise PolicyError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(document, MutableMapping):
        raise PolicyError(f"{label} must be a JSON object: {path}")
    return document


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(child) for child in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(child) for child in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(child) for child in value]
    if isinstance(value, frozenset):
        return [_deep_thaw(child) for child in value]
    return deepcopy(value)


def _resolve_authority_path(policy_path: Path, declared_path: str) -> Path:
    relative = Path(declared_path)
    if relative.is_absolute():
        return relative
    candidates: list[Path] = []
    for base in (Path.cwd(), *policy_path.resolve().parents):
        candidate = base / relative
        if candidate not in candidates:
            candidates.append(candidate)
        if candidate.exists():
            return candidate
    checked = ", ".join(str(path) for path in candidates)
    raise PolicyError(f"Authority file not found: {declared_path}; checked: {checked}")


# ---------------------------------------------------------------------------
# Signed attestations
# ---------------------------------------------------------------------------


def _parse_time(value: str, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError(f"Invalid {label} timestamp: {value}") from exc
    if result.tzinfo is None:
        raise PolicyError(f"{label} timestamp must include timezone: {value}")
    return result.astimezone(timezone.utc)


def new_request(
    *,
    service: str,
    operation: Operation,
    requested_tenant_alias: str,
    target_alias: str,
    ttl_seconds: int = 300,
    now: datetime | None = None,
) -> OperationRequest:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return OperationRequest(
        request_id=f"AKOS-REQ-{uuid4()}",
        service=service,
        operation=operation,
        requested_tenant_alias=requested_tenant_alias,
        target_alias=target_alias,
        issued_at=current.isoformat(timespec="microseconds"),
        expires_at=(current + timedelta(seconds=ttl_seconds)).isoformat(timespec="microseconds"),
    )


def _canonical_attestation_payload(
    *,
    kind: AttestationKind,
    request: OperationRequest,
    route: str,
    outcome: AttestationOutcome,
    issued_at: str,
    expires_at: str,
    nonce: str,
    claims: Sequence[tuple[str, str]],
) -> bytes:
    payload = {
        "kind": kind.value,
        "request_id": request.request_id,
        "service": request.service,
        "route": route,
        "operation": request.operation.value,
        "outcome": outcome.value,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "claims": sorted((str(key), str(value)) for key, value in claims),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_attestation(
    *,
    key: bytes,
    kind: AttestationKind,
    request: OperationRequest,
    route: str,
    outcome: AttestationOutcome,
    claims: Mapping[str, str],
    ttl_seconds: int = 120,
    now: datetime | None = None,
) -> Attestation:
    if not key:
        raise PolicyError("Attestation key is required")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = current.isoformat(timespec="microseconds")
    expires_at = (current + timedelta(seconds=ttl_seconds)).isoformat(timespec="microseconds")
    nonce = str(uuid4())
    claim_pairs = tuple(sorted((str(name), str(value)) for name, value in claims.items()))
    payload = _canonical_attestation_payload(
        kind=kind,
        request=request,
        route=route,
        outcome=outcome,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        claims=claim_pairs,
    )
    signature = hmac.new(key, payload, sha256).hexdigest()
    return Attestation(
        kind=kind,
        request_id=request.request_id,
        service=request.service,
        route=route,
        operation=request.operation,
        outcome=outcome,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        claims=claim_pairs,
        signature=signature,
    )


class AttestationVerifier:
    def __init__(
        self,
        key: bytes,
        *,
        max_clock_skew_seconds: int = 30,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if not key:
            raise PolicyError("Attestation verification key is required")
        self._key = bytes(key)
        self._max_clock_skew = timedelta(seconds=max_clock_skew_seconds)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def verify(
        self,
        attestation: Attestation,
        request: OperationRequest,
        route: str,
    ) -> Mapping[str, str]:
        if (
            attestation.request_id != request.request_id
            or attestation.service != request.service
            or attestation.route != route
            or attestation.operation != request.operation
        ):
            raise PolicyError("Attestation is not bound to the current request and route")
        issued = _parse_time(attestation.issued_at, "attestation issued_at")
        expires = _parse_time(attestation.expires_at, "attestation expires_at")
        now = self._now_fn().astimezone(timezone.utc)
        if issued > now + self._max_clock_skew:
            raise PolicyError("Attestation was issued in the future")
        if expires < now - self._max_clock_skew:
            raise PolicyError("Attestation expired")
        request_issued = _parse_time(request.issued_at, "request issued_at")
        request_expires = _parse_time(request.expires_at, "request expires_at")
        if issued < request_issued - self._max_clock_skew or expires > request_expires + self._max_clock_skew:
            raise PolicyError("Attestation falls outside the request execution window")
        payload = _canonical_attestation_payload(
            kind=attestation.kind,
            request=request,
            route=route,
            outcome=attestation.outcome,
            issued_at=attestation.issued_at,
            expires_at=attestation.expires_at,
            nonce=attestation.nonce,
            claims=attestation.claims,
        )
        expected = hmac.new(self._key, payload, sha256).hexdigest()
        if not hmac.compare_digest(expected, attestation.signature):
            raise PolicyError("Attestation signature is invalid")
        return attestation.claim_map()


# ---------------------------------------------------------------------------
# Route authority
# ---------------------------------------------------------------------------


class RouteAuthority:
    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = _deep_freeze(document)
        route_authority = self.document.get("route_authority")
        if not isinstance(route_authority, Mapping) or not route_authority:
            raise PolicyError("Route map lacks route_authority")
        self._services = route_authority
        self._validate()

    def _validate(self) -> None:
        for service, config in self._services.items():
            if not isinstance(config, Mapping):
                raise PolicyError(f"Route authority for {service} must be an object")
            primary = tuple(config.get("primary", ()))
            fallback = tuple(config.get("fallback", ()))
            if not primary:
                raise PolicyError(f"Route authority for {service} has no primary route")
            names = primary + fallback
            if len(names) != len(set(names)):
                raise PolicyError(f"Route authority for {service} contains duplicate route names")
            allowed_operations = tuple(config.get("allowed_operations", ()))
            if not allowed_operations:
                raise PolicyError(f"Route authority for {service} has no allowed_operations")
            unknown_operations = set(allowed_operations).difference(operation.value for operation in Operation)
            if unknown_operations:
                raise PolicyError(
                    f"Route authority for {service} has unknown operations: "
                    f"{sorted(unknown_operations)}"
                )

    def service_config(self, service: str) -> Mapping[str, Any]:
        try:
            return self._services[service]
        except KeyError as exc:
            raise PolicyError(f"Unknown service in route authority: {service}") from exc

    def role_for(self, service: str, route: str) -> str | None:
        config = self.service_config(service)
        if route in config.get("primary", ()):
            return "primary"
        if route in config.get("fallback", ()):
            return "fallback"
        return None

    def primary_routes(self, service: str) -> tuple[str, ...]:
        return tuple(self.service_config(service).get("primary", ()))

    def allowed_operation(self, service: str, operation: Operation) -> bool:
        return operation.value in self.service_config(service).get("allowed_operations", ())

    def requires_local_device(self, service: str, operation: Operation) -> bool:
        local_operations = self.service_config(service).get("local_device_operations", ())
        return operation.value in local_operations

    def export_document(self) -> dict[str, Any]:
        return _deep_thaw(self.document)


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


class ConnectorPolicy:
    REQUIRED_TOP_LEVEL = {
        "schema_version",
        "policy_id",
        "required_authorities",
        "authority",
        "roles",
        "verification_states",
        "route_selection",
        "operation_classes",
        "authorization_gates",
        "completion_gates",
        "audit",
    }
    REQUIRED_ATTESTATION_GATES = {
        "identity",
        "account_affinity",
        "current_runtime_probe",
    }

    def __init__(
        self,
        *,
        document: Mapping[str, Any],
        authority: RouteAuthority,
        verifier: AttestationVerifier,
        repair_sink: RepairSink,
        trusted_policy_sha256: str,
    ) -> None:
        self.document = _deep_freeze(document)
        self.authority = authority
        self._verifier = verifier
        self._repair_sink = repair_sink
        self.trusted_policy_sha256 = trusted_policy_sha256
        self._validate()

    @classmethod
    def load(
        cls,
        policy_path: str | Path,
        *,
        trusted_policy_sha256: str,
        attestation_key: bytes,
        repair_sink: RepairSink,
    ) -> "ConnectorPolicy":
        path_obj = Path(policy_path)
        actual_policy_sha = file_sha256(path_obj)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", trusted_policy_sha256):
            raise PolicyError("Trusted policy SHA-256 must be a 64-character hex digest")
        if not hmac.compare_digest(actual_policy_sha, trusted_policy_sha256.lower()):
            raise PolicyError(
                "Policy digest does not match the externally pinned trust root: "
                f"expected={trusted_policy_sha256.lower()}, actual={actual_policy_sha}"
            )
        sidecar = path_obj.with_suffix(path_obj.suffix + ".sha256")
        if not verify_sha256_sidecar(path_obj, sidecar):
            raise PolicyError(f"Policy sidecar mismatch: {sidecar}")

        document = _load_json(path_obj, "Policy")
        required_authorities = document.get("required_authorities")
        authority_entries = document.get("authority")
        if not isinstance(required_authorities, list) or not required_authorities:
            raise PolicyError("Policy required_authorities must be a non-empty list")
        if not isinstance(authority_entries, Mapping):
            raise PolicyError("Policy authority must be an object")

        verified_paths: dict[str, Path] = {}
        for name in required_authorities:
            entry = authority_entries.get(name)
            if not isinstance(entry, Mapping):
                raise PolicyError(f"Required authority is missing or malformed: {name}")
            declared_path = entry.get("path")
            expected_digest = entry.get("sha256")
            if not isinstance(declared_path, str) or not isinstance(expected_digest, str):
                raise PolicyError(f"Required authority lacks path or sha256: {name}")
            authority_path = _resolve_authority_path(path_obj.resolve(), declared_path)
            actual_digest = file_sha256(authority_path)
            if not hmac.compare_digest(actual_digest, expected_digest.lower()):
                raise PolicyError(
                    f"Authority digest mismatch for {name}: "
                    f"expected={expected_digest.lower()}, actual={actual_digest}"
                )
            authority_sidecar = authority_path.with_suffix(authority_path.suffix + ".sha256")
            if not verify_sha256_sidecar(authority_path, authority_sidecar):
                raise PolicyError(f"Authority sidecar mismatch for {name}: {authority_sidecar}")
            verified_paths[name] = authority_path

        route_map_path = verified_paths.get("canonical_route_map")
        if route_map_path is None:
            raise PolicyError("canonical_route_map must be a required authority")
        route_map = RouteAuthority(_load_json(route_map_path, "Canonical route map"))

        return cls(
            document=document,
            authority=route_map,
            verifier=AttestationVerifier(attestation_key),
            repair_sink=repair_sink,
            trusted_policy_sha256=trusted_policy_sha256.lower(),
        )

    def _validate(self) -> None:
        missing = self.REQUIRED_TOP_LEVEL.difference(self.document)
        if missing:
            raise PolicyError(f"Policy missing required keys: {sorted(missing)}")
        sensitive_fields = find_sensitive_policy_fields(self.document)
        if sensitive_fields:
            raise PolicyError(
                f"Policy contains forbidden sensitive fields: {sensitive_fields}"
            )
        states = set(self.document["verification_states"])
        allowed = set(self.document["route_selection"].get("runtime_states_allowed", ()))
        if not allowed or not allowed.issubset(states):
            raise PolicyError("route_selection.runtime_states_allowed is invalid")
        preferred = self.document["route_selection"].get("preferred_state")
        if preferred not in allowed:
            raise PolicyError("route_selection.preferred_state must be allowed")
        required_authorities = tuple(self.document["required_authorities"])
        if len(required_authorities) != len(set(required_authorities)):
            raise PolicyError("required_authorities contains duplicates")
        for operation in Operation:
            if operation.value not in self.document["operation_classes"]:
                raise PolicyError(f"Policy lacks operation class: {operation.value}")
            if operation.value not in self.document["authorization_gates"]:
                raise PolicyError(f"Policy lacks authorization gates: {operation.value}")
            if operation.value not in self.document["completion_gates"]:
                raise PolicyError(f"Policy lacks completion gates: {operation.value}")

    def export_document(self) -> dict[str, Any]:
        return _deep_thaw(self.document)

    def _required_capability(self, operation: Operation) -> str:
        return str(self.document["operation_classes"][operation.value]["capability"])

    def _authorization_gates(self, request: OperationRequest) -> frozenset[str]:
        gates = set(self.document["authorization_gates"][request.operation.value])
        if self.authority.requires_local_device(request.service, request.operation):
            gates.update(self.document["authorization_gates"]["local_device"])
        return frozenset(gates)

    def _completion_gates(self, request: OperationRequest) -> frozenset[str]:
        gates = set(self.document["completion_gates"][request.operation.value])
        if self.authority.requires_local_device(request.service, request.operation):
            gates.update(self.document["completion_gates"]["local_device"])
        return frozenset(gates)

    def _valid_attestation_proofs(
        self,
        request: OperationRequest,
        observation: RouteObservation,
    ) -> tuple[frozenset[str], bool, bool]:
        proofs: set[str] = set()
        runtime_passed = False
        runtime_failed = False
        for attestation in observation.attestations:
            try:
                claims = self._verifier.verify(attestation, request, observation.route)
            except PolicyError:
                continue
            if attestation.kind is AttestationKind.IDENTITY:
                if (
                    attestation.outcome is AttestationOutcome.PASSED
                    and claims.get("authenticated_tenant_alias")
                    == request.requested_tenant_alias
                ):
                    proofs.add("identity")
            elif attestation.kind is AttestationKind.TENANT_AFFINITY:
                if (
                    attestation.outcome is AttestationOutcome.PASSED
                    and claims.get("requested_tenant_alias")
                    == request.requested_tenant_alias
                    and claims.get("authenticated_tenant_alias")
                    == request.requested_tenant_alias
                ):
                    proofs.add("account_affinity")
            elif attestation.kind is AttestationKind.RUNTIME_PROBE:
                if attestation.outcome is AttestationOutcome.PASSED:
                    proofs.add("current_runtime_probe")
                    runtime_passed = True
                else:
                    runtime_failed = True
        return frozenset(proofs), runtime_passed, runtime_failed

    def _repair(
        self,
        request: OperationRequest,
        failures: Mapping[str, Sequence[str]],
    ) -> RepairInstruction:
        frozen_failures = MappingProxyType(
            {name: tuple(gates) for name, gates in failures.items()}
        )
        instruction = RepairInstruction(
            instruction_id=f"AKOS-REPAIR-{uuid4()}",
            request_id=request.request_id,
            service=request.service,
            operation=request.operation.value,
            created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            failures=frozen_failures,
        )
        self._repair_sink.enqueue(instruction)
        return instruction

    def authorize(
        self,
        request: OperationRequest,
        observations: Iterable[RouteObservation],
    ) -> RouteDecision:
        if not self.authority.allowed_operation(request.service, request.operation):
            failures = {"__intent__": ("operation_not_allowed_for_service",)}
            repair = self._repair(request, failures)
            raise NoApprovedRoute(request, failures, repair)

        now = datetime.now(timezone.utc)
        if _parse_time(request.expires_at, "request expires_at") < now:
            failures = {"__intent__": ("request_expired",)}
            repair = self._repair(request, failures)
            raise NoApprovedRoute(request, failures, repair)

        observation_list = list(observations)
        if not observation_list:
            failures = {"__route_resolution__": ("no_candidates",)}
            repair = self._repair(request, failures)
            raise NoApprovedRoute(request, failures, repair)

        names = [observation.route for observation in observation_list]
        duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicate_names:
            failures = {
                name: ("duplicate_route_name",)
                for name in duplicate_names
            }
            repair = self._repair(request, failures)
            raise NoApprovedRoute(request, failures, repair)

        allowed_states = set(self.document["route_selection"]["runtime_states_allowed"])
        preferred_state = str(self.document["route_selection"]["preferred_state"])
        required_capability = self._required_capability(request.operation)
        required_authorization = self._authorization_gates(request)
        required_completion = self._completion_gates(request)

        approved_primary: list[tuple[RouteObservation, frozenset[str]]] = []
        approved_fallback: list[tuple[RouteObservation, frozenset[str]]] = []
        rejected: dict[str, tuple[str, ...]] = {}
        primary_runtime_failures: set[str] = set()

        for observation in observation_list:
            role = self.authority.role_for(request.service, observation.route)
            failures: list[str] = []
            if role is None:
                failures.append("route_not_in_verified_authority")
            if observation.verification not in allowed_states:
                failures.append("runtime_verification_state")
            if role == "fallback" and observation.verification != preferred_state:
                failures.append("fallback_not_verified")
            if required_capability not in observation.capabilities:
                failures.append("capability")

            attestation_proofs, runtime_passed, runtime_failed = (
                self._valid_attestation_proofs(request, observation)
            )
            if role == "primary" and runtime_failed:
                primary_runtime_failures.add(observation.route)

            combined_proofs = set(observation.proofs)
            combined_proofs.update(attestation_proofs)
            missing_authorization = sorted(required_authorization.difference(combined_proofs))
            failures.extend(f"missing_proof:{gate}" for gate in missing_authorization)

            missing_completion_contract = sorted(
                required_completion.difference(observation.completion_capabilities)
            )
            failures.extend(
                f"completion_contract:{gate}" for gate in missing_completion_contract
            )

            if runtime_passed is False and "current_runtime_probe" in required_authorization:
                if "missing_proof:current_runtime_probe" not in failures:
                    failures.append("missing_proof:current_runtime_probe")

            if failures:
                rejected[observation.route] = tuple(dict.fromkeys(failures))
            elif role == "primary":
                approved_primary.append((observation, frozenset(combined_proofs)))
            elif role == "fallback":
                approved_fallback.append((observation, frozenset(combined_proofs)))

        selected: tuple[RouteObservation, frozenset[str]] | None = None
        selected_role = ""
        if approved_primary:
            selected = sorted(
                approved_primary,
                key=lambda pair: pair[0].verification == preferred_state,
                reverse=True,
            )[0]
            selected_role = "primary"
        elif approved_fallback:
            primary_routes = self.authority.primary_routes(request.service)
            if not primary_routes:
                for observation, _ in approved_fallback:
                    rejected[observation.route] = ("primary_route_missing",)
            elif not set(primary_routes).intersection(primary_runtime_failures):
                for observation, _ in approved_fallback:
                    rejected[observation.route] = ("primary_failure_not_proven",)
            else:
                selected = approved_fallback[0]
                selected_role = "fallback"

        if selected is None:
            repair = self._repair(request, rejected)
            raise NoApprovedRoute(request, rejected, repair)

        observation, proofs = selected
        return RouteDecision(
            request=request,
            selected_route=observation.route,
            selected_role=selected_role,
            verification=observation.verification,
            authorization_proofs=proofs,
            required_completion_gates=required_completion,
            rejected_routes=MappingProxyType(dict(rejected)),
        )

    def build_rejected_receipt(
        self,
        decision: RouteDecision,
        failed_gates: Iterable[str],
    ) -> AuditReceipt:
        gates = tuple(dict.fromkeys(str(gate) for gate in failed_gates)) or (
            "connector_runtime_failure",
        )
        return AuditReceipt(
            receipt_id=f"AKOS-RECEIPT-{uuid4()}",
            timestamp=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            request_id=decision.request.request_id,
            service=decision.request.service,
            route=decision.selected_route,
            operation=decision.request.operation.value,
            decision="rejected",
            verification=decision.verification,
            authorization_proofs=tuple(sorted(decision.authorization_proofs)),
            completion_proofs=(),
            artifact_hashes=MappingProxyType({}),
            failed_gates=gates,
        )

    def record_failure(
        self,
        decision: RouteDecision,
        failed_gates: Iterable[str],
    ) -> tuple[AuditReceipt, RepairInstruction]:
        """Persist a rejected receipt without replacing the original exception."""
        receipt = self.build_rejected_receipt(decision, failed_gates)
        repair = self._repair(
            decision.request,
            {decision.selected_route: receipt.failed_gates},
        )
        return receipt, repair

    def complete(
        self,
        decision: RouteDecision,
        *,
        completion_proofs: Iterable[str],
        artifact_hashes: Mapping[str, str] | None = None,
        artifact_bytes: Mapping[str, bytes] | None = None,
    ) -> AuditReceipt:
        provided = frozenset(str(proof) for proof in completion_proofs)
        missing = sorted(decision.required_completion_gates.difference(provided))
        sanitized_hashes = sanitize_audit_metadata(dict(artifact_hashes or {}))
        if "artifact_hash" in decision.required_completion_gates:
            verified_artifacts = dict(artifact_bytes or {})
            if not verified_artifacts or not all(
                isinstance(name, str) and isinstance(value, bytes)
                for name, value in verified_artifacts.items()
            ):
                missing.append("artifact_hash")
            else:
                sanitized_hashes = {
                    str(name): sha256(value).hexdigest()
                    for name, value in verified_artifacts.items()
                }

        now = datetime.now(timezone.utc)
        if _parse_time(decision.request.expires_at, "request expires_at") < now:
            missing.append("execution_window_expired")
        timestamp = now.isoformat(timespec="microseconds")
        if missing:
            receipt = AuditReceipt(
                receipt_id=f"AKOS-RECEIPT-{uuid4()}",
                timestamp=timestamp,
                request_id=decision.request.request_id,
                service=decision.request.service,
                route=decision.selected_route,
                operation=decision.request.operation.value,
                decision="rejected",
                verification=decision.verification,
                authorization_proofs=tuple(sorted(decision.authorization_proofs)),
                completion_proofs=tuple(sorted(provided)),
                artifact_hashes=MappingProxyType(dict(sanitized_hashes)),
                failed_gates=tuple(dict.fromkeys(missing)),
            )
            repair = self._repair(
                decision.request,
                {decision.selected_route: receipt.failed_gates},
            )
            raise CompletionRejected(receipt, repair)

        return AuditReceipt(
            receipt_id=f"AKOS-RECEIPT-{uuid4()}",
            timestamp=timestamp,
            request_id=decision.request.request_id,
            service=decision.request.service,
            route=decision.selected_route,
            operation=decision.request.operation.value,
            decision="approved",
            verification=decision.verification,
            authorization_proofs=tuple(sorted(decision.authorization_proofs)),
            completion_proofs=tuple(sorted(provided)),
            artifact_hashes=MappingProxyType(dict(sanitized_hashes)),
            failed_gates=(),
        )



def receipt_to_dict(receipt: AuditReceipt) -> dict[str, Any]:
    return sanitize_audit_metadata(
        {
            "receipt_id": receipt.receipt_id,
            "timestamp": receipt.timestamp,
            "request_id": receipt.request_id,
            "service": receipt.service,
            "route": receipt.route,
            "operation": receipt.operation,
            "decision": receipt.decision,
            "verification": receipt.verification,
            "authorization_proofs": list(receipt.authorization_proofs),
            "completion_proofs": list(receipt.completion_proofs),
            "artifact_hashes": dict(receipt.artifact_hashes),
            "failed_gates": list(receipt.failed_gates),
        }
    )


def repair_instruction_to_dict(instruction: RepairInstruction) -> dict[str, Any]:
    return sanitize_audit_metadata(
        {
            "instruction_id": instruction.instruction_id,
            "request_id": instruction.request_id,
            "service": instruction.service,
            "operation": instruction.operation,
            "created_at": instruction.created_at,
            "failures": dict(instruction.failures),
            "action": instruction.action,
        }
    )

def validate_policy_file(
    policy_path: str | Path,
    *,
    trusted_policy_sha256: str,
    attestation_key: bytes = b"validation-only-key",
) -> dict[str, str]:
    sink = InMemoryRepairSink()
    policy = ConnectorPolicy.load(
        policy_path,
        trusted_policy_sha256=trusted_policy_sha256,
        attestation_key=attestation_key,
        repair_sink=sink,
    )
    result: dict[str, str] = {"policy": policy.trusted_policy_sha256}
    for name in policy.document["required_authorities"]:
        result[str(name)] = str(policy.document["authority"][name]["sha256"])
    return result


def _main(argv: Sequence[str]) -> int:
    if len(argv) != 3 or argv[1] != "--validate":
        print(
            "usage: python -m smithery_control_plane.runtime.connector_policy "
            "--validate <policy.json>",
            file=sys.stderr,
        )
        return 2
    trusted_digest = os.environ.get("AKOS_POLICY_SHA256", "")
    if not trusted_digest:
        print(
            "validation failed: AKOS_POLICY_SHA256 external trust root is required",
            file=sys.stderr,
        )
        return 1
    try:
        verified = validate_policy_file(
            Path(argv[2]),
            trusted_policy_sha256=trusted_digest,
        )
    except PolicyError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"validated {argv[2]} policy={verified['policy']} "
        f"authorities={len(verified) - 1}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
