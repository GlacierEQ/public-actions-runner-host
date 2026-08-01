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
    plan = legacy_fixture.plan()
    plan.update(
        {
            "domain": "code",
            "action": "code.validate-governance",
            "adapter": "monolith_ip_governance",
        }
    )
    return plan


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
    legacy_result = json.loads(result_path.read_text(encoding="utf-8"))

    for generated in results.iterdir():
        generated.unlink()

    assert domain_adapter.run(plan, workspace, result_path) == 0
    domain_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert domain_result == legacy_result


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
    assert domain_adapter.run(canonical_plan(), workspace, result_path) == 0

    result = json.loads(result_path.read_text(encoding="utf-8"))
    domain_adapter.verify_canonical_result(result)
    assert set(result) == domain_adapter.RESULT_KEYS
    assert result["status"] == "completed"
    assert result["domain"] == "code"
    assert result["action"] == "code.validate-governance"
    assert result["adapter"] == "monolith_ip_governance"
    assert result["adapter_sha256"] == domain_adapter.adapter_bundle_sha256()
    assert result["token_profile"] == "private-source-read"
    assert result["source_repo"] == "GlacierEQ/monolith"
    assert result["resolved_source_sha"] == SOURCE_SHA
    assert result["test_count"] == 1
    assert result["secret_scan"]["status"] == "passed"
    assert len(result["checks"]) == 9
    assert all(set(check) == {"name", "status", "output_sha256"} for check in result["checks"])
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


def test_canonical_receipt_tampering_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_fixture.write_fixture(workspace)
    result_path = tmp_path / "result.json"

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    assert domain_adapter.run(canonical_plan(), workspace, result_path) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["status"] = "failed"

    with pytest.raises(ValueError, match="receipt hash mismatch"):
        domain_adapter.verify_canonical_result(result)


def test_cross_domain_canonical_plan_is_blocked_before_execution(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "blocked.json"
    plan = canonical_plan()
    plan["domain"] = "analysis"

    assert domain_adapter.run(plan, tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    domain_adapter.verify_canonical_result(result)
    assert result["status"] == "blocked"
    assert result["domain"] == "code"
    assert result["action"] == "code.validate-governance"
    assert result["checks"] == []
    assert result["reason"] == "cross-domain execution is forbidden"


def test_caller_selected_repository_and_adapter_are_blocked(tmp_path: Path) -> None:
    repository_result = tmp_path / "repository-blocked.json"
    repository_plan = canonical_plan()
    repository_plan["source_repo"] = "GlacierEQ/other-private-repo"
    assert domain_adapter.run(repository_plan, tmp_path, repository_result) != 0
    repository_value = json.loads(repository_result.read_text(encoding="utf-8"))
    domain_adapter.verify_canonical_result(repository_value)
    assert "catalog-bound repository" in repository_value["reason"]

    adapter_result = tmp_path / "adapter-blocked.json"
    adapter_plan = canonical_plan()
    adapter_plan["adapter"] = "arbitrary_shell"
    assert domain_adapter.run(adapter_plan, tmp_path, adapter_result) != 0
    adapter_value = json.loads(adapter_result.read_text(encoding="utf-8"))
    domain_adapter.verify_canonical_result(adapter_value)
    assert "caller-selected adapter" in adapter_value["reason"]


def test_wrapper_and_execution_core_are_hash_bound() -> None:
    hashes = domain_adapter.adapter_control_hashes()
    assert set(hashes) == {"domain_wrapper", "legacy_execution_core"}
    assert all(len(value) == 64 for value in hashes.values())
    assert all(set(value) <= set("0123456789abcdef") for value in hashes.values())
    assert len(domain_adapter.adapter_bundle_sha256()) == 64
