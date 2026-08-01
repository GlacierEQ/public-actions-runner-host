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


def test_wrapper_and_legacy_adapter_produce_identical_bounded_results(
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


def test_canonical_action_delegates_to_the_existing_execution_core(
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
    plan.update(
        {
            "domain": "code",
            "action": "code.validate-governance",
            "adapter": "monolith_ip_governance",
        }
    )

    assert domain_adapter.run(plan, workspace, result_path) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["action"] == "code.validate-governance"
    assert result["resolved_source_sha"] == SOURCE_SHA
    assert result["test_count"] == 1


def test_cross_domain_plan_is_blocked_before_execution(tmp_path: Path) -> None:
    result_path = tmp_path / "blocked.json"
    plan = legacy_fixture.plan()
    plan["domain"] = "analysis"

    assert domain_adapter.run(plan, tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["domain"] == "code"
    assert result["canonical_action"] == "code.validate-governance"
    assert result["reason"] == "cross-domain execution is forbidden"


def test_caller_selected_repository_and_adapter_are_blocked(tmp_path: Path) -> None:
    repository_result = tmp_path / "repository-blocked.json"
    repository_plan = legacy_fixture.plan()
    repository_plan["source_repo"] = "GlacierEQ/other-private-repo"
    assert domain_adapter.run(repository_plan, tmp_path, repository_result) != 0
    assert "catalog-bound repository" in json.loads(
        repository_result.read_text(encoding="utf-8")
    )["reason"]

    adapter_result = tmp_path / "adapter-blocked.json"
    adapter_plan = legacy_fixture.plan()
    adapter_plan["adapter"] = "arbitrary_shell"
    assert domain_adapter.run(adapter_plan, tmp_path, adapter_result) != 0
    assert "caller-selected adapter" in json.loads(
        adapter_result.read_text(encoding="utf-8")
    )["reason"]


def test_wrapper_and_execution_core_are_hash_bound() -> None:
    hashes = domain_adapter.adapter_control_hashes()
    assert set(hashes) == {"domain_wrapper", "legacy_execution_core"}
    assert all(len(value) == 64 for value in hashes.values())
    assert all(set(value) <= set("0123456789abcdef") for value in hashes.values())
