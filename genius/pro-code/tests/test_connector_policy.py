from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from smithery_control_plane.runtime.connector_policy import (
    AttestationKind,
    AttestationOutcome,
    CompletionRejected,
    ConnectorPolicy,
    InMemoryRepairSink,
    JsonlRepairSink,
    NoApprovedRoute,
    Operation,
    PolicyError,
    RouteObservation,
    sanitize_audit_metadata,
    sign_attestation,
    new_request,
    validate_policy_file,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "smithery_control_plane/config/akos_connector_policy.json"
POLICY_SHA = "0fff66f147f693da7c14b9a87f1bf8e185e6fb746dcf09f901a89770a57d2ebe"
KEY = b"unit-test-attestation-key"
TENANT = "primary_control_account"


def load_policy(sink: InMemoryRepairSink | JsonlRepairSink | None = None) -> ConnectorPolicy:
    return ConnectorPolicy.load(
        POLICY_PATH,
        trusted_policy_sha256=POLICY_SHA,
        attestation_key=KEY,
        repair_sink=sink or InMemoryRepairSink(),
    )


def make_request(
    service: str = "public_legal_research",
    operation: Operation = Operation.READ,
    *,
    now: datetime | None = None,
):
    return new_request(
        service=service,
        operation=operation,
        requested_tenant_alias=TENANT,
        target_alias="bounded_control_target",
        now=now,
    )


def attestations(
    request,
    route: str,
    *,
    runtime: AttestationOutcome = AttestationOutcome.PASSED,
    authenticated_tenant: str = TENANT,
    now: datetime | None = None,
):
    return (
        sign_attestation(
            key=KEY,
            kind=AttestationKind.IDENTITY,
            request=request,
            route=route,
            outcome=AttestationOutcome.PASSED,
            claims={"authenticated_tenant_alias": authenticated_tenant},
            now=now,
        ),
        sign_attestation(
            key=KEY,
            kind=AttestationKind.TENANT_AFFINITY,
            request=request,
            route=route,
            outcome=AttestationOutcome.PASSED,
            claims={
                "requested_tenant_alias": request.requested_tenant_alias,
                "authenticated_tenant_alias": authenticated_tenant,
            },
            now=now,
        ),
        sign_attestation(
            key=KEY,
            kind=AttestationKind.RUNTIME_PROBE,
            request=request,
            route=route,
            outcome=runtime,
            claims={"probe_scope": f"{request.service}:{request.operation.value}"},
            now=now,
        ),
    )


def observation(
    request,
    route: str,
    *,
    verification: str = "Verified",
    capability: str = "read",
    proofs=frozenset({"current_schema"}),
    completion_capabilities=frozenset({"bounded_read", "validated_response"}),
    runtime: AttestationOutcome = AttestationOutcome.PASSED,
    authenticated_tenant: str = TENANT,
    now: datetime | None = None,
):
    return RouteObservation(
        route=route,
        verification=verification,
        capabilities=frozenset({capability}),
        proofs=frozenset(proofs),
        completion_capabilities=frozenset(completion_capabilities),
        attestations=attestations(
            request,
            route,
            runtime=runtime,
            authenticated_tenant=authenticated_tenant,
            now=now,
        ),
    )


def test_external_policy_pin_and_all_authorities_validate() -> None:
    verified = validate_policy_file(
        POLICY_PATH,
        trusted_policy_sha256=POLICY_SHA,
        attestation_key=KEY,
    )
    assert verified == {
        "policy": POLICY_SHA,
        "canonical_route_map": "deb9169905f22b7226f241bc958323172af4a213dcdc2f1b539b58f1f0cc3b15",
        "coverage_milestone": "38b12960446a214ab4b0eb140a1fe9f59e1c88a0ac65406cb2fcc251a1293719",
    }


def test_wrong_or_missing_external_policy_pin_fails_closed() -> None:
    with pytest.raises(PolicyError, match="externally pinned"):
        ConnectorPolicy.load(
            POLICY_PATH,
            trusted_policy_sha256="0" * 64,
            attestation_key=KEY,
            repair_sink=InMemoryRepairSink(),
        )
    with pytest.raises(PolicyError, match="64-character"):
        ConnectorPolicy.load(
            POLICY_PATH,
            trusted_policy_sha256="",
            attestation_key=KEY,
            repair_sink=InMemoryRepairSink(),
        )


def test_every_required_authority_must_be_hash_verifiable(tmp_path: Path) -> None:
    document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    document["authority"]["canonical_route_map"].pop("sha256")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(document), encoding="utf-8")
    digest = __import__("hashlib").sha256(policy_path.read_bytes()).hexdigest()
    policy_path.with_suffix(".json.sha256").write_text(
        f"{digest}  policy.json\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="lacks path or sha256"):
        ConnectorPolicy.load(
            policy_path,
            trusted_policy_sha256=digest,
            attestation_key=KEY,
            repair_sink=InMemoryRepairSink(),
        )


def test_validated_policy_and_route_authority_are_immutable() -> None:
    policy = load_policy()
    with pytest.raises(TypeError):
        policy.document["route_selection"]["runtime_states_allowed"] += ("Blocked",)
    with pytest.raises(TypeError):
        policy.authority.document["route_authority"]["web_search"]["primary"] = ("fake",)
    exported = policy.export_document()
    exported["route_selection"]["runtime_states_allowed"].append("Blocked")
    assert "Blocked" not in policy.document["route_selection"]["runtime_states_allowed"]


def test_route_role_is_derived_from_verified_authority() -> None:
    policy = load_policy()
    request = make_request()
    forged = observation(request, "Unknown Caller-Named Primary")
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [forged])
    assert "route_not_in_verified_authority" in exc.value.failures[forged.route]


def test_duplicate_route_names_fail_closed_and_enqueue_repair() -> None:
    sink = InMemoryRepairSink()
    policy = load_policy(sink)
    request = make_request()
    route = "CourtListener / RECAP Adapter"
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [observation(request, route), observation(request, route)])
    assert exc.value.failures[route] == ("duplicate_route_name",)
    assert sink.instructions[-1].request_id == request.request_id


def test_identity_and_affinity_are_bound_to_requested_tenant_alias() -> None:
    policy = load_policy()
    request = make_request()
    route = "CourtListener / RECAP Adapter"
    bad = observation(request, route, authenticated_tenant="secondary_casebrain_tenant")
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [bad])
    failures = exc.value.failures[route]
    assert "missing_proof:identity" in failures
    assert "missing_proof:account_affinity" in failures


def test_runtime_probe_is_bound_to_current_request() -> None:
    policy = load_policy()
    request = make_request()
    other_request = make_request()
    route = "CourtListener / RECAP Adapter"
    stale = RouteObservation(
        route=route,
        verification="Verified",
        capabilities=frozenset({"read"}),
        proofs=frozenset({"current_schema"}),
        completion_capabilities=frozenset({"bounded_read", "validated_response"}),
        attestations=attestations(other_request, route),
    )
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [stale])
    assert "missing_proof:current_runtime_probe" in exc.value.failures[route]


def test_expired_attestation_is_rejected() -> None:
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    request = make_request(now=old)
    route = "CourtListener / RECAP Adapter"
    policy = load_policy()
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [observation(request, route, now=old)])
    assert exc.value.failures["__intent__"] == ("request_expired",)


def test_primary_route_wins_over_verified_fallback() -> None:
    policy = load_policy()
    request = make_request(service="web_search")
    primary = observation(request, "Smithery Exa Search")
    fallback = observation(request, "Tavily")
    decision = policy.authorize(request, [fallback, primary])
    assert decision.selected_route == "Smithery Exa Search"
    assert decision.selected_role == "primary"


def test_partial_fallback_is_never_admitted() -> None:
    policy = load_policy()
    request = make_request(service="web_search")
    primary = observation(
        request,
        "Smithery Exa Search",
        runtime=AttestationOutcome.FAILED,
    )
    fallback = observation(request, "Tavily", verification="Partial")
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [primary, fallback])
    assert "fallback_not_verified" in exc.value.failures["Tavily"]


def test_fallback_requires_signed_current_primary_failure() -> None:
    policy = load_policy()
    request = make_request(service="web_search")
    primary = RouteObservation(
        route="Smithery Exa Search",
        verification="Verified",
        capabilities=frozenset({"read"}),
        proofs=frozenset({"current_schema"}),
        completion_capabilities=frozenset({"bounded_read", "validated_response"}),
        attestations=attestations(request, "Smithery Exa Search")[:2],
    )
    fallback = observation(request, "Tavily")
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [primary, fallback])
    assert exc.value.failures["Tavily"] == ("primary_failure_not_proven",)


def test_verified_fallback_runs_only_after_signed_primary_failure() -> None:
    policy = load_policy()
    request = make_request(service="web_search")
    primary = observation(
        request,
        "Smithery Exa Search",
        runtime=AttestationOutcome.FAILED,
    )
    fallback = observation(request, "Tavily")
    decision = policy.authorize(request, [primary, fallback])
    assert decision.selected_route == "Tavily"
    assert decision.selected_role == "fallback"


def test_all_preflight_promotion_gates_are_enforced() -> None:
    policy = load_policy()
    request = make_request(service="documents", operation=Operation.WRITE)
    route = "Notion Control Plane"
    incomplete = observation(
        request,
        route,
        capability="write",
        proofs=frozenset({"current_schema"}),
        completion_capabilities=frozenset(
            {
                "scoped_write",
                "matching_readback",
                "cleanup_or_approved_retention",
                "artifact_hash",
                "registry_update",
            }
        ),
    )
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [incomplete])
    assert "missing_proof:isolated_target" in exc.value.failures[route]


def test_completion_contract_is_checked_before_execution() -> None:
    policy = load_policy()
    request = make_request(service="documents", operation=Operation.WRITE)
    route = "Notion Control Plane"
    incomplete_contract = observation(
        request,
        route,
        capability="write",
        proofs=frozenset({"current_schema", "isolated_target"}),
        completion_capabilities=frozenset({"scoped_write"}),
    )
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [incomplete_contract])
    assert any(
        gate.startswith("completion_contract:")
        for gate in exc.value.failures[route]
    )


def test_local_operation_requires_all_local_preflight_and_completion_contracts() -> None:
    policy = load_policy()
    request = make_request(
        service="desktop_and_local_files",
        operation=Operation.LOCAL_READ,
    )
    route = "Smithery Remote Desktop Commander"
    incomplete = observation(
        request,
        route,
        capability="local_read",
        proofs=frozenset({"current_schema", "device_online", "same_run_ping"}),
        completion_capabilities=frozenset({"bounded_read", "validated_response"}),
    )
    with pytest.raises(NoApprovedRoute) as exc:
        policy.authorize(request, [incomplete])
    failures = exc.value.failures[route]
    assert "missing_proof:approved_root" in failures
    assert "missing_proof:policy_scope" in failures
    assert "completion_contract:matching_local_and_cloud_receipts" in failures


def test_successful_read_completion_emits_approved_receipt() -> None:
    policy = load_policy()
    request = make_request()
    route = "CourtListener / RECAP Adapter"
    decision = policy.authorize(request, [observation(request, route)])
    receipt = policy.complete(
        decision,
        completion_proofs={"bounded_read", "validated_response"},
    )
    assert receipt.decision == "approved"
    assert receipt.failed_gates == ()


def test_failed_completion_cannot_emit_approved_receipt() -> None:
    sink = InMemoryRepairSink()
    policy = load_policy(sink)
    request = make_request(service="documents", operation=Operation.WRITE)
    route = "Notion Control Plane"
    decision = policy.authorize(
        request,
        [
            observation(
                request,
                route,
                capability="write",
                proofs=frozenset({"current_schema", "isolated_target"}),
                completion_capabilities=frozenset(
                    {
                        "scoped_write",
                        "matching_readback",
                        "cleanup_or_approved_retention",
                        "artifact_hash",
                        "registry_update",
                    }
                ),
            )
        ],
    )
    with pytest.raises(CompletionRejected) as exc:
        policy.complete(
            decision,
            completion_proofs={"scoped_write"},
            artifact_hashes={},
        )
    assert exc.value.receipt.decision == "rejected"
    assert "matching_readback" in exc.value.receipt.failed_gates
    assert sink.instructions[-1].request_id == request.request_id


def test_write_completion_requires_real_sha256_artifact_hashes() -> None:
    policy = load_policy()
    request = make_request(service="documents", operation=Operation.WRITE)
    route = "Notion Control Plane"
    capabilities = frozenset(
        {
            "scoped_write",
            "matching_readback",
            "cleanup_or_approved_retention",
            "artifact_hash",
            "registry_update",
        }
    )
    decision = policy.authorize(
        request,
        [
            observation(
                request,
                route,
                capability="write",
                proofs=frozenset({"current_schema", "isolated_target"}),
                completion_capabilities=capabilities,
            )
        ],
    )
    with pytest.raises(CompletionRejected):
        policy.complete(
            decision,
            completion_proofs=capabilities,
            artifact_hashes={"artifact": "not-a-sha"},
        )
    receipt = policy.complete(
        decision,
        completion_proofs=capabilities,
        artifact_hashes={"artifact": "a" * 64},
    )
    assert receipt.decision == "approved"


def test_repair_queue_is_hash_chained_and_sanitized(tmp_path: Path) -> None:
    queue = JsonlRepairSink(tmp_path / "repair.jsonl")
    policy = load_policy(queue)
    request = make_request()
    with pytest.raises(NoApprovedRoute):
        policy.authorize(request, [])
    records = [
        json.loads(line)
        for line in (tmp_path / "repair.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["previous_sha256"] == "0" * 64
    assert len(records[0]["record_sha256"]) == 64
    assert records[0]["instruction"]["request_id"] == request.request_id
    assert queue.verify_chain() is True


def test_repair_queue_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "repair.jsonl"
    queue = JsonlRepairSink(path)
    policy = load_policy(queue)
    with pytest.raises(NoApprovedRoute):
        policy.authorize(make_request(), [])
    record = json.loads(path.read_text(encoding="utf-8"))
    record["instruction"]["action"] = "tampered"
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(PolicyError, match="digest mismatch"):
        queue.verify_chain()


def test_policy_rejects_sensitive_fields() -> None:
    document = load_policy().export_document()
    document["unsafe"] = {"api_token": "must-not-be-stored"}
    current = load_policy()
    with pytest.raises(PolicyError, match="forbidden sensitive fields"):
        ConnectorPolicy(
            document=document,
            authority=current.authority,
            verifier=current._verifier,
            repair_sink=InMemoryRepairSink(),
            trusted_policy_sha256=POLICY_SHA,
        )


def test_sensitive_key_matching_preserves_benign_telemetry() -> None:
    value = {
        "token": "remove",
        "api_token": "remove",
        "token_count": 4,
        "email_verified": True,
        "password_rotation_status": "current",
    }
    assert sanitize_audit_metadata(value) == {
        "token_count": 4,
        "email_verified": True,
        "password_rotation_status": "current",
    }


def test_free_form_credentials_and_identifiers_are_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue123"
    value = {
        "message": (
            f"probe used credential {jwt}; bearer abcDEF1234567890XYZ_token_value "
            "and setup https://example.test/setup?token=secret"
        ),
        "operator": "person@example.com",
        "resource": "3a7b1e4f-3223-8144-9d05-f10fb0541d01",
        "public_url": "https://example.test/reference/123",
        "service": "public_legal_research",
    }
    result = sanitize_audit_metadata(value)
    assert jwt not in result["message"]
    assert "abcDEF1234567890XYZ_token_value" not in result["message"]
    assert "[REDACTED_SETUP_URL]" in result["message"]
    assert result["operator"] == "[REDACTED_EMAIL]"
    assert result["resource"] == "[REDACTED_IDENTIFIER]"
    assert result["public_url"] == "https://example.test/reference/123"
    assert result["service"] == "public_legal_research"


def test_request_and_attestation_dataclasses_are_immutable() -> None:
    request = make_request()
    with pytest.raises(FrozenInstanceError):
        request.service = "other"  # type: ignore[misc]



def test_repair_queue_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "repair.jsonl"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="not a JSON object"):
        JsonlRepairSink(path).verify_chain()


def test_repair_queue_cached_tail_detects_external_change(tmp_path: Path) -> None:
    path = tmp_path / "repair.jsonl"
    queue = JsonlRepairSink(path)
    policy = load_policy(queue)
    for _ in range(3):
        with pytest.raises(NoApprovedRoute):
            policy.authorize(make_request(), [])
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3
    assert queue.verify_chain() is True
    path.write_text(path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(PolicyError):
        queue.verify_chain()


def test_sha1_is_not_accepted_as_artifact_sha256() -> None:
    policy = load_policy()
    request = make_request(service="documents", operation=Operation.WRITE)
    route = "Notion Control Plane"
    capabilities = frozenset(
        {"scoped_write", "matching_readback", "cleanup_or_approved_retention", "artifact_hash", "registry_update"}
    )
    decision = policy.authorize(
        request,
        [observation(request, route, capability="write", proofs={"current_schema", "isolated_target"}, completion_capabilities=capabilities)],
    )
    with pytest.raises(CompletionRejected):
        policy.complete(decision, completion_proofs=capabilities, artifact_hashes={"artifact": "a" * 40})


def test_record_failure_returns_rejected_receipt_and_repair() -> None:
    sink = InMemoryRepairSink()
    policy = load_policy(sink)
    request = make_request()
    decision = policy.authorize(request, [observation(request, "CourtListener / RECAP Adapter")])
    receipt, repair = policy.record_failure(decision, ["connector_runtime_failure"])
    assert receipt.decision == "rejected"
    assert receipt.failed_gates == ("connector_runtime_failure",)
    assert sink.instructions[-1] == repair
