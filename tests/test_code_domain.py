from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import monolith_ip_governance_adapter as legacy
import test_monolith_ip_governance_adapter as legacy_fixture
from domains.code.adapters import monolith_ip_governance as domain_adapter

SOURCE_SHA = "a" * 40


def canonical_plan() -> dict:
    return {
        "job_id": "monolith-ip-governance-test",
        "domain": "code",
        "action": "code.validate-governance",
        "source_ref": "overhaul/ip-control-plane-v1",
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rehash(result: dict) -> None:
    result.pop("receipt_sha256", None)
    result["receipt_sha256"] = domain_adapter.canonical_sha256(result)


def test_adapter_contract_keys_match_registered_schemas() -> None:
    job_schema = read_json(
        ROOT / "domains" / "code" / "schemas" / "job.schema.json"
    )
    result_schema = read_json(
        ROOT / "domains" / "code" / "schemas" / "result.schema.json"
    )

    assert set(job_schema["properties"]) == domain_adapter.CANONICAL_JOB_KEYS
    assert set(job_schema["required"]) == {
        "job_id",
        "domain",
        "action",
        "source_ref",
    }
    assert job_schema["additionalProperties"] is False

    assert set(result_schema["properties"]) == domain_adapter.RESULT_KEYS
    assert set(result_schema["required"]) == domain_adapter.RESULT_KEYS
    assert result_schema["additionalProperties"] is False


def test_schema_valid_canonical_plan_normalizes_to_fixed_legacy_identity() -> None:
    plan = canonical_plan()
    normalized = domain_adapter.validate_plan(plan)
    assert set(plan) == {"job_id", "domain", "action", "source_ref"}
    assert normalized == {
        "job_id": plan["job_id"],
        "pillar": "D",
        "action": "code.validate-governance",
        "adapter": "monolith_ip_governance",
        "task": "test",
        "source_repo": "GlacierEQ/monolith",
        "source_ref": plan["source_ref"],
        "target_repo": "GlacierEQ/monolith",
        "expected_source_sha": None,
        "approval_id": None,
    }


def test_legacy_alias_preserves_byte_for_byte_bounded_result_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)

    results = tmp_path / "results"
    results.mkdir()
    result_path = results / "result.json"

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    plan = legacy_fixture.plan()

    assert legacy.run(plan, workspace, result_path) == 0
    legacy_result = read_json(result_path)

    for generated in results.iterdir():
        generated.unlink()

    assert domain_adapter.run(plan, workspace, result_path) == 0
    assert read_json(result_path) == legacy_result


def test_canonical_action_emits_a_bounded_hash_verified_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)
    results = tmp_path / "results"
    results.mkdir()
    result_path = results / "result.json"

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    plan = canonical_plan()
    plan["expected_source_sha"] = SOURCE_SHA
    plan["approval_id"] = "OwnerApproval01"
    assert domain_adapter.run(plan, workspace, result_path) == 0

    result = read_json(result_path)
    domain_adapter.verify_canonical_result(result)
    assert set(result) == domain_adapter.RESULT_KEYS
    assert result["status"] == "completed"
    assert result["domain"] == "code"
    assert result["action"] == "code.validate-governance"
    assert result["adapter"] == "monolith_ip_governance"
    assert result["adapter_sha256"] == domain_adapter.adapter_bundle_sha256()
    assert result["token_profile"] == "private-source-read"
    assert result["source_repo"] == "GlacierEQ/monolith"
    assert result["expected_source_sha"] == SOURCE_SHA
    assert result["approval_id"] == "OwnerApproval01"
    assert result["resolved_source_sha"] == SOURCE_SHA
    assert result["test_count"] == 1
    assert result["secret_scan"]["status"] == "passed"
    assert len(result["checks"]) == 9
    assert all(
        set(check) == {"name", "status", "output_sha256"}
        for check in result["checks"]
    )
    assert result["artifact_references"] == [
        {
            "kind": "secret-scan-report",
            "sha256": result["secret_scan"]["report_sha256"],
        }
    ]
    assert len(result["legacy_result_sha256"]) == 64
    assert "steps" not in result
    assert "output_tail" not in json.dumps(result)
    assert not list(results.glob(".*.legacy.*.json"))


def test_schema_valid_job_without_resolved_sha_returns_bounded_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.delenv("APEX_RESOLVED_SOURCE_SHA", raising=False)

    assert domain_adapter.run(canonical_plan(), workspace, result_path) != 0
    result = read_json(result_path)
    domain_adapter.verify_canonical_result(result)
    assert result["status"] == "blocked"
    assert result["source_ref"] == canonical_plan()["source_ref"]
    assert result["legacy_result_sha256"] is not None
    assert "KeyError" not in result["reason"]


def test_canonical_receipt_hash_tampering_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)
    result_path = tmp_path / "result.json"

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    assert domain_adapter.run(canonical_plan(), workspace, result_path) == 0
    result = read_json(result_path)
    result["status"] = "failed"

    with pytest.raises(ValueError, match="receipt hash mismatch"):
        domain_adapter.verify_canonical_result(result)


def test_rehashed_semantically_forged_receipt_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)
    result_path = tmp_path / "result.json"

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    assert domain_adapter.run(canonical_plan(), workspace, result_path) == 0
    result = read_json(result_path)
    result["checks"][0]["status"] = "blocked"
    rehash(result)

    with pytest.raises(ValueError, match="incomplete checks"):
        domain_adapter.verify_canonical_result(result)


def test_cross_domain_canonical_plan_is_blocked_before_execution(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "blocked.json"
    plan = canonical_plan()
    plan["domain"] = "analysis"

    assert domain_adapter.run(plan, tmp_path, result_path) != 0
    result = read_json(result_path)
    domain_adapter.verify_canonical_result(result)
    assert result["status"] == "blocked"
    assert result["domain"] == "code"
    assert result["action"] == "code.validate-governance"
    assert result["checks"] == []
    assert result["reason"] == "canonical plan domain must be code"


def test_unsafe_identifiers_are_sanitized_in_blocked_receipts(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "blocked.json"
    plan = canonical_plan()
    plan["job_id"] = "../../etc/passwd"
    plan["source_ref"] = "../private/.git"

    assert domain_adapter.run(plan, tmp_path, result_path) != 0
    result = read_json(result_path)
    domain_adapter.verify_canonical_result(result)
    assert result["job_id"] == "invalid-domain-plan"
    assert result["source_ref"] == ""
    assert result["reason"] == "job_id is invalid"


def test_expected_source_sha_mismatch_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "blocked.json"
    plan = canonical_plan()
    plan["expected_source_sha"] = "b" * 40
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)

    assert domain_adapter.run(plan, tmp_path, result_path) != 0
    result = read_json(result_path)
    domain_adapter.verify_canonical_result(result)
    assert result["expected_source_sha"] == "b" * 40
    assert "does not match resolved source" in result["reason"]


def test_caller_selected_legacy_fields_are_blocked(tmp_path: Path) -> None:
    for field, value in (
        ("source_repo", "GlacierEQ/other-private-repo"),
        ("adapter", "arbitrary_shell"),
        ("pillar", "A"),
        ("task", "deploy"),
    ):
        result_path = tmp_path / f"{field}-blocked.json"
        plan = canonical_plan()
        plan[field] = value
        assert domain_adapter.run(plan, tmp_path, result_path) != 0
        result = read_json(result_path)
        domain_adapter.verify_canonical_result(result)
        assert result["reason"] == "canonical plan contains unsupported fields"


def test_dangling_symlink_result_directory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)
    missing_target = tmp_path / "missing-result-target"
    symlink_parent = tmp_path / "results"
    symlink_parent.symlink_to(missing_target)

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    assert (
        domain_adapter.run(
            canonical_plan(), workspace, symlink_parent / "result.json"
        )
        != 0
    )
    assert not missing_target.exists()


def test_wrapper_and_execution_core_are_hash_bound() -> None:
    hashes = domain_adapter.adapter_control_hashes()
    assert set(hashes) == {"domain_wrapper", "legacy_execution_core"}
    assert all(len(value) == 64 for value in hashes.values())
    assert all(set(value) <= set("0123456789abcdef") for value in hashes.values())
    assert len(domain_adapter.adapter_bundle_sha256()) == 64
