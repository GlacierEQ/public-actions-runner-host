from __future__ import annotations

from pathlib import Path

import pytest

from smithery_control_plane.runtime.connector_gateway import (
    ExecutionOutcome,
    PolicyGateway,
    ProbeEvidence,
    compute_outcome,
    read_outcome,
    resolve_actor_operation_spec,
    resolve_mcp_tool_spec,
)
from smithery_control_plane.runtime.connector_policy import (
    ConnectorPolicy,
    InMemoryRepairSink,
    NoApprovedRoute,
    Operation,
    PolicyError,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "smithery_control_plane/config/akos_connector_policy.json"
POLICY_SHA = "0fff66f147f693da7c14b9a87f1bf8e185e6fb746dcf09f901a89770a57d2ebe"
KEY = b"gateway-test-key"
TENANT = "primary_control_account"


def gateway(*, allowed_roots: tuple[Path, ...] = ()) -> PolicyGateway:
    policy = ConnectorPolicy.load(
        POLICY_PATH,
        trusted_policy_sha256=POLICY_SHA,
        attestation_key=KEY,
        repair_sink=InMemoryRepairSink(),
    )
    return PolicyGateway(
        policy=policy,
        attestation_key=KEY,
        tenant_alias=TENANT,
        allowed_local_roots=allowed_roots,
    )


def test_tool_classification_ignores_caller_operation_flags() -> None:
    spec = resolve_mcp_tool_spec(
        "fileboss_search",
        {
            "operation": "write",
            "temporary": True,
            "local": True,
            "query": "bounded",
        },
    )
    assert spec.operation is Operation.READ
    assert spec.service == "fileboss_mcp_tools"


def test_dynamic_actions_are_closed_and_explicit() -> None:
    assert resolve_mcp_tool_spec(
        "fileboss_case_evidence",
        {"action": "get"},
    ).operation is Operation.READ
    assert resolve_mcp_tool_spec(
        "fileboss_case_evidence",
        {"action": "add"},
    ).operation is Operation.WRITE
    with pytest.raises(PolicyError, match="Unsupported evidence action"):
        resolve_mcp_tool_spec("fileboss_case_evidence", {"action": "execute_anything"})


@pytest.mark.asyncio
async def test_safe_read_executes_through_gateway_and_completes() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_search", {"query": "HRS 571-46"})
    called = False

    async def callback():
        nonlocal called
        called = True
        return read_outcome({"results": [{"title": "bounded"}]})

    result, receipt = await policy_gateway.execute(
        spec=spec,
        arguments={"query": "HRS 571-46"},
        target_alias="bounded_search",
        probe=lambda: ProbeEvidence(True, TENANT),
        callback=callback,
    )
    assert called is True
    assert result["results"][0]["title"] == "bounded"
    assert receipt.decision == "approved"


@pytest.mark.asyncio
async def test_unproven_write_is_blocked_before_callback() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_memory_store", {"content": "control"})
    called = False

    async def callback():
        nonlocal called
        called = True
        return ExecutionOutcome(
            result={"stored": True},
            completion_proofs=frozenset(),
        )

    with pytest.raises(NoApprovedRoute) as exc:
        await policy_gateway.execute(
            spec=spec,
            arguments={"content": "control"},
            target_alias="memory_control",
            probe=lambda: ProbeEvidence(True, TENANT),
            callback=callback,
        )
    assert called is False
    assert any(
        gate.startswith("completion_contract:")
        for gate in exc.value.failures[spec.route]
    )


@pytest.mark.asyncio
async def test_local_path_outside_approved_roots_is_blocked_before_execution(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    policy_gateway = gateway(allowed_roots=(approved,))
    spec = resolve_mcp_tool_spec("fileboss_local_analyze", {"path": str(outside)})
    called = False

    async def callback():
        nonlocal called
        called = True
        return read_outcome({"files": []})

    with pytest.raises(PolicyError, match="outside FILEBOSS_ALLOWED_ROOTS"):
        await policy_gateway.execute(
            spec=spec,
            arguments={"path": str(outside)},
            target_alias="local_analysis",
            probe=lambda: ProbeEvidence(True, TENANT),
            callback=callback,
        )
    assert called is False


@pytest.mark.asyncio
async def test_local_read_without_cloud_receipt_contract_is_blocked_before_io(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    policy_gateway = gateway(allowed_roots=(approved,))
    spec = resolve_mcp_tool_spec("fileboss_local_analyze", {"path": str(approved)})
    called = False

    async def callback():
        nonlocal called
        called = True
        return read_outcome({"files": []})

    with pytest.raises(NoApprovedRoute) as exc:
        await policy_gateway.execute(
            spec=spec,
            arguments={"path": str(approved)},
            target_alias="local_analysis",
            probe=lambda: ProbeEvidence(True, TENANT),
            callback=callback,
        )
    assert called is False
    assert (
        "completion_contract:matching_local_and_cloud_receipts"
        in exc.value.failures[spec.route]
    )


@pytest.mark.asyncio
async def test_compute_tool_uses_compute_completion_contract() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_motion_draft", {"facts": "bounded"})

    result, receipt = await policy_gateway.execute(
        spec=spec,
        arguments={"facts": "bounded"},
        target_alias="motion_draft",
        probe=lambda: ProbeEvidence(True, TENANT),
        callback=lambda: compute_outcome({"draft": "text"}),
    )
    assert result["draft"] == "text"
    assert receipt.decision == "approved"


def test_actor_ingest_is_blocked_until_write_readback_contract_exists() -> None:
    spec = resolve_actor_operation_spec("ingest")
    assert spec.operation is Operation.WRITE
    assert spec.completion_capabilities == frozenset()


def test_environment_loader_requires_external_trust_material(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="AKOS_POLICY_SHA256"):
        PolicyGateway.from_environment(POLICY_PATH, environment={})
    with pytest.raises(PolicyError, match="AKOS_ATTESTATION_HMAC_KEY"):
        PolicyGateway.from_environment(
            POLICY_PATH,
            environment={
                "AKOS_POLICY_SHA256": POLICY_SHA,
                "AKOS_TENANT_ALIAS": TENANT,
            },
        )



@pytest.mark.asyncio
async def test_probe_runs_before_callback() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_search", {"query": "bounded"})
    order = []

    async def probe():
        order.append("probe")
        return ProbeEvidence(True, TENANT)

    async def callback():
        order.append("callback")
        return read_outcome({"results": []})

    _, receipt = await policy_gateway.execute(
        spec=spec,
        arguments={"query": "bounded"},
        target_alias="bounded_search",
        probe=probe,
        callback=callback,
    )
    assert order == ["probe", "callback"]
    assert receipt.decision == "approved"


@pytest.mark.asyncio
async def test_failed_probe_blocks_callback() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_search", {})
    called = False

    async def callback():
        nonlocal called
        called = True
        return read_outcome({"results": []})

    with pytest.raises(NoApprovedRoute):
        await policy_gateway.execute(
            spec=spec,
            arguments={},
            target_alias="bounded_search",
            probe=lambda: ProbeEvidence(False, ""),
            callback=callback,
        )
    assert called is False


@pytest.mark.asyncio
async def test_callback_exception_is_preserved_with_rejected_receipt() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_search", {})
    original = RuntimeError("connector exploded")

    def callback():
        raise original

    with pytest.raises(RuntimeError) as exc:
        await policy_gateway.execute(
            spec=spec,
            arguments={},
            target_alias="bounded_search",
            probe=lambda: ProbeEvidence(True, TENANT),
            callback=callback,
        )
    assert exc.value is original
    assert exc.value.akos_rejected_receipt.decision == "rejected"
    assert exc.value.akos_repair_instruction.request_id == exc.value.akos_rejected_receipt.request_id


def test_read_outcome_rejects_structured_errors_and_oversize() -> None:
    for result in ({"status": "error"}, {"error": "bad"}, {"status_code": 500}, {"isError": True}):
        with pytest.raises(PolicyError):
            read_outcome(result)
    with pytest.raises(PolicyError, match="bounded response"):
        read_outcome({"data": "x" * (1024 * 1024 + 1)})


@pytest.mark.asyncio
async def test_boolean_probe_cannot_self_assert_identity() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_search", {"query": "bounded"})
    called = False

    async def callback():
        nonlocal called
        called = True
        return read_outcome({"results": []})

    with pytest.raises(NoApprovedRoute) as exc:
        await policy_gateway.execute(
            spec=spec,
            arguments={"query": "bounded"},
            target_alias="bounded_search",
            probe=lambda: True,  # type: ignore[return-value]
            callback=callback,
        )
    assert called is False
    assert "missing_proof:identity" in exc.value.failures[spec.route]


@pytest.mark.asyncio
async def test_probe_identity_must_match_requested_tenant() -> None:
    policy_gateway = gateway()
    spec = resolve_mcp_tool_spec("fileboss_search", {"query": "bounded"})
    with pytest.raises(NoApprovedRoute) as exc:
        await policy_gateway.execute(
            spec=spec,
            arguments={"query": "bounded"},
            target_alias="bounded_search",
            probe=lambda: ProbeEvidence(True, "other-tenant"),
            callback=lambda: read_outcome({"results": []}),
        )
    assert "missing_proof:identity" in exc.value.failures[spec.route]
    assert "missing_proof:account_affinity" in exc.value.failures[spec.route]


def test_read_outcome_rejects_nested_structured_errors() -> None:
    bad_results = (
        {"sources": {"memory": {"status": "error"}}},
        {"results": [{"data": {"errors": ["upstream failed"]}}]},
        {"content": [{"statusCode": 503}]},
        {"nested": {"isError": True}},
    )
    for result in bad_results:
        with pytest.raises(PolicyError):
            read_outcome(result)


@pytest.mark.asyncio
async def test_original_exception_survives_repair_persistence_failure() -> None:
    class FailingSink:
        def enqueue(self, instruction) -> None:
            raise OSError("repair disk offline")

    policy = ConnectorPolicy.load(
        POLICY_PATH,
        trusted_policy_sha256=POLICY_SHA,
        attestation_key=KEY,
        repair_sink=FailingSink(),
    )
    policy_gateway = PolicyGateway(
        policy=policy,
        attestation_key=KEY,
        tenant_alias=TENANT,
    )
    spec = resolve_mcp_tool_spec("fileboss_search", {})
    original = RuntimeError("connector exploded")

    def callback():
        raise original

    with pytest.raises(RuntimeError) as exc:
        await policy_gateway.execute(
            spec=spec,
            arguments={},
            target_alias="bounded_search",
            probe=lambda: ProbeEvidence(True, TENANT),
            callback=callback,
        )
    assert exc.value is original
    assert exc.value.akos_rejected_receipt.decision == "rejected"
    assert "repair disk offline" in str(exc.value.akos_repair_persistence_error)
