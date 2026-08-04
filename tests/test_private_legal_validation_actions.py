from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dispatcher import domain_registry
from domains.code.adapters import (
    casey_legal_mcp_validate,
    monolith_company_registry_validate,
    monolith_legal_live_validate,
)
from scripts import action_face_plan, apex_catalog_runner

SHA = "a" * 40
ATTESTATION = {
    "resolved_source_sha": SHA,
    "tracked_clean": True,
    "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}

ACTIONS = (
    (
        "code.monolith.validate-legal-live-reconciliation",
        "monolith_legal_live_validate",
        "GlacierEQ/monolith",
    ),
    (
        "code.monolith.validate-company-engineered-registry",
        "monolith_company_registry_validate",
        "GlacierEQ/monolith",
    ),
    (
        "code.casey-legal-mcp.validate-v2",
        "casey_legal_mcp_validate",
        "GlacierEQ/casey-legal-mcp-server",
    ),
)


def build_plan(action: str, adapter: str, repository: str) -> dict:
    return {
        "job_id": "LegalRun01",
        "pillar": "C",
        "action": action,
        "adapter": adapter,
        "task": "test",
        "source_repo": repository,
        "source_ref": SHA,
        "target_repo": repository,
        "approval_id": "",
        "workflow_run_id": "1",
        "workflow_run_attempt": "1",
        "trigger_actor": "GlacierEQ",
        "trigger_actor_id": "1",
        "event_name": "workflow_dispatch",
        "execution_repo": "GlacierEQ/public-actions-runner-host",
        "public_runner_sha": SHA,
    }


def write_files(workspace: Path, required: tuple[str, ...]) -> None:
    for relative in required:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "# fixture\n", encoding="utf-8")


def test_domain_registry_resolves_all_three_actions() -> None:
    loaded = domain_registry.validate_registry(ROOT)
    for action, adapter, repository in ACTIONS:
        contract = loaded[action]
        assert contract["adapter"] == adapter
        assert contract["targetRepository"] == repository
        assert contract["executionMode"] == "source-read-only"
        assert contract["tokenProfileContract"]["permissions"] == {"contents": "read"}
        assert contract["tokenProfileContract"]["exposeCredentialToWorkload"] is False


def test_action_face_plans_exact_private_validation_actions(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    for action, adapter, repository in ACTIONS:
        result = action_face_plan.build_plan(
            str(event),
            {
                "job_id": "LegalPlan01",
                "pillar": "C",
                "action": action,
                "source_ref": SHA,
            },
        )
        assert result["action"] == action
        assert result["adapter"] == adapter
        assert result["source_repo"] == repository
        assert result["target_repo"] == repository
        assert result["source_ref"] == SHA
        assert result["task"] == "test"


def test_actions_reject_mutable_refs(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    for action, _, _ in ACTIONS:
        with pytest.raises(SystemExit, match="requires a full lowercase commit SHA"):
            action_face_plan.build_plan(
                str(event),
                {
                    "job_id": "Mutable01",
                    "pillar": "C",
                    "action": action,
                    "source_ref": "main",
                },
            )


def test_catalog_dispatch_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(monolith_legal_live_validate, "run", lambda *_: calls.append("legal") or 0)
    monkeypatch.setattr(monolith_company_registry_validate, "run", lambda *_: calls.append("company") or 0)
    monkeypatch.setattr(casey_legal_mcp_validate, "run", lambda *_: calls.append("mcp") or 0)

    expected = ("legal", "company", "mcp")
    for (action, adapter, repository), marker in zip(ACTIONS, expected, strict=True):
        assert apex_catalog_runner.run_registered_specialization(
            build_plan(action, adapter, repository), Path("workspace"), Path("result.json")
        ) == 0
        assert calls[-1] == marker


def test_monolith_legal_adapter_runs_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, monolith_legal_live_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(monolith_legal_live_validate, "attest_workspace", lambda *_: dict(ATTESTATION))
    monkeypatch.setattr(monolith_legal_live_validate, "build_environment", lambda *_: {})
    monkeypatch.setattr(
        monolith_legal_live_validate.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok\n"),
    )

    value = monolith_legal_live_validate.run(
        build_plan(ACTIONS[0][0], ACTIONS[0][1], ACTIONS[0][2]),
        workspace,
        result_path,
    )
    assert value == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert len(result["steps"]) == 3
    assert result["validated_gates"] == [
        "legal-live-registry",
        "legal-live-board",
        "integration-contract",
    ]


def test_company_adapter_runs_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, monolith_company_registry_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(monolith_company_registry_validate, "attest_workspace", lambda *_: dict(ATTESTATION))
    monkeypatch.setattr(monolith_company_registry_validate, "build_environment", lambda *_: {})
    monkeypatch.setattr(
        monolith_company_registry_validate.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok\n"),
    )

    value = monolith_company_registry_validate.run(
        build_plan(ACTIONS[1][0], ACTIONS[1][1], ACTIONS[1][2]),
        workspace,
        result_path,
    )
    assert value == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert len(result["steps"]) == 4


def test_casey_mcp_adapter_requires_node_20_and_runs_policy_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, casey_legal_mcp_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(casey_legal_mcp_validate, "attest_workspace", lambda *_: dict(ATTESTATION))
    monkeypatch.setattr(casey_legal_mcp_validate, "build_environment", lambda *_: {})
    monkeypatch.setattr(casey_legal_mcp_validate.shutil, "which", lambda _: "/usr/bin/node")

    outputs = iter(("v20.19.0\n", "", "", "12 tests passed\n"))
    monkeypatch.setattr(
        casey_legal_mcp_validate.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=next(outputs)),
    )

    value = casey_legal_mcp_validate.run(
        build_plan(ACTIONS[2][0], ACTIONS[2][1], ACTIONS[2][2]),
        workspace,
        result_path,
    )
    assert value == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["runtime"] == {"node_major": 20}
    assert len(result["steps"]) == 4


def test_cross_bound_plan_is_blocked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "result.json"
    bad = build_plan(ACTIONS[2][0], ACTIONS[2][1], ACTIONS[2][2])
    bad["target_repo"] = "GlacierEQ/monolith"
    assert casey_legal_mcp_validate.run(bad, workspace, result_path) == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "target_repo identity mismatch"
