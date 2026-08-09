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
CALLABLE_ACTIONS = (ACTIONS[0], ACTIONS[2])


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
        if path.suffix == ".json":
            content = "{}\n"
        elif path.suffix == ".py":
            content = "pass\n"
        elif path.suffix == ".js":
            content = "export {};\n"
        else:
            content = "# fixture\n"
        path.write_text(content, encoding="utf-8")


def assert_run_contract(kwargs: dict, workspace: Path) -> None:
    assert kwargs["cwd"] == workspace
    assert kwargs["text"] is True
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None
    assert kwargs["check"] is False
    assert kwargs["shell"] is False
    assert kwargs["env"] == {}


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
    for action, adapter, repository in CALLABLE_ACTIONS:
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
    for action, _, _ in CALLABLE_ACTIONS:
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
    monkeypatch.setattr(
        monolith_legal_live_validate,
        "run",
        lambda *_: calls.append("legal") or 0,
    )
    monkeypatch.setattr(
        monolith_company_registry_validate,
        "run",
        lambda *_: calls.append("company") or 0,
    )
    monkeypatch.setattr(
        casey_legal_mcp_validate,
        "run",
        lambda *_: calls.append("mcp") or 0,
    )

    expected = ("legal", "company", "mcp")
    for (action, adapter, repository), marker in zip(ACTIONS, expected, strict=True):
        assert apex_catalog_runner.run_registered_specialization(
            build_plan(action, adapter, repository),
            Path("workspace"),
            Path("result.json"),
        ) == 0
        assert calls[-1] == marker


def test_monolith_legal_adapter_matches_declared_full_promotion_gate() -> None:
    assert monolith_legal_live_validate.commands() == [
        [
            sys.executable,
            "-m",
            "py_compile",
            "scripts/validate_legal_live_reconciliation.py",
            "tests/test_legal_live_reconciliation.py",
        ],
        [sys.executable, "scripts/validate_legal_live_reconciliation.py"],
        [sys.executable, "-m", "unittest", "tests.test_legal_live_reconciliation"],
        [sys.executable, "scripts/validate_legal_case.py"],
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_legal_case",
            "tests.test_legal_spine",
            "tests.test_sync_legal_spines",
        ],
        [sys.executable, "scripts/sync_legal_spines.py", "--check"],
        ["git", "diff", "--check"],
    ]


def test_monolith_legal_adapter_runs_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, monolith_legal_live_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        monolith_legal_live_validate,
        "attest_workspace",
        lambda *_: dict(ATTESTATION),
    )
    monkeypatch.setattr(
        monolith_legal_live_validate,
        "build_environment",
        lambda *_: {},
    )
    expected = monolith_legal_live_validate.commands()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        assert command == expected[len(calls)]
        assert_run_contract(kwargs, workspace)
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr(monolith_legal_live_validate.subprocess, "run", fake_run)

    value = monolith_legal_live_validate.run(
        build_plan(ACTIONS[0][0], ACTIONS[0][1], ACTIONS[0][2]),
        workspace,
        result_path,
    )
    assert value == 0
    assert calls == expected
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert len(result["steps"]) == 7
    assert result["validated_gates"] == [
        "legal-live-reconciliation",
        "legal-case",
        "legal-spine-sync",
        "tracked-diff-check",
    ]


def test_company_adapter_provisions_and_runs_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, monolith_company_registry_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        monolith_company_registry_validate,
        "attest_workspace",
        lambda *_: dict(ATTESTATION),
    )
    monkeypatch.setattr(
        monolith_company_registry_validate,
        "build_environment",
        lambda *_: {},
    )
    expected = monolith_company_registry_validate.commands(result_path, "LegalRun01")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        assert command == expected[len(calls)]
        assert_run_contract(kwargs, workspace)
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr(monolith_company_registry_validate.subprocess, "run", fake_run)

    value = monolith_company_registry_validate.run(
        build_plan(ACTIONS[1][0], ACTIONS[1][1], ACTIONS[1][2]),
        workspace,
        result_path,
    )
    assert value == 0
    assert calls == expected
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["runtime"] == {"pytest": monolith_company_registry_validate.PYTEST_VERSION}
    assert len(result["steps"]) == 4
    assert result["steps"][0]["command"][1:3] == ["-m", "venv"]
    assert "pip" in result["steps"][1]["command"]
    assert result["steps"][3]["command"][1:3] == ["-m", "pytest"]


def test_casey_mcp_adapter_requires_node_20_and_runs_policy_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, casey_legal_mcp_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        casey_legal_mcp_validate,
        "attest_workspace",
        lambda *_: dict(ATTESTATION),
    )
    monkeypatch.setattr(
        casey_legal_mcp_validate,
        "build_environment",
        lambda *_: {},
    )
    monkeypatch.setattr(
        casey_legal_mcp_validate.shutil,
        "which",
        lambda _: "/usr/bin/node",
    )

    expected = casey_legal_mcp_validate.commands("/usr/bin/node")
    outputs = iter(("v20.19.0\n", "", "", "12 tests passed\n"))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        assert command == expected[len(calls)]
        assert_run_contract(kwargs, workspace)
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=next(outputs))

    monkeypatch.setattr(casey_legal_mcp_validate.subprocess, "run", fake_run)

    value = casey_legal_mcp_validate.run(
        build_plan(ACTIONS[2][0], ACTIONS[2][1], ACTIONS[2][2]),
        workspace,
        result_path,
    )
    assert value == 0
    assert calls == expected
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["runtime"] == {"node_major": 20}
    assert len(result["steps"]) == 4


def test_adapter_failure_stops_sequence_and_records_failed_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_files(workspace, monolith_legal_live_validate.REQUIRED_PATHS)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        monolith_legal_live_validate,
        "attest_workspace",
        lambda *_: dict(ATTESTATION),
    )
    monkeypatch.setattr(
        monolith_legal_live_validate,
        "build_environment",
        lambda *_: {},
    )
    expected = monolith_legal_live_validate.commands()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        assert command == expected[0]
        assert_run_contract(kwargs, workspace)
        calls.append(command)
        return SimpleNamespace(returncode=7, stdout="validation failed\n")

    monkeypatch.setattr(monolith_legal_live_validate.subprocess, "run", fake_run)

    assert monolith_legal_live_validate.run(
        build_plan(ACTIONS[0][0], ACTIONS[0][1], ACTIONS[0][2]),
        workspace,
        result_path,
    ) == 2
    assert calls == expected[:1]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["steps"] == [
        {
            "command": expected[0],
            "exit_code": 7,
            "output_sha256": result["steps"][0]["output_sha256"],
            "output_tail": "validation failed\n",
            "status": "failed",
        }
    ]


@pytest.mark.parametrize(
    ("adapter_module", "action_index"),
    (
        (monolith_legal_live_validate, 0),
        (monolith_company_registry_validate, 1),
        (casey_legal_mcp_validate, 2),
    ),
)
def test_resolved_sha_must_equal_requested_source_ref(
    adapter_module, action_index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "b" * 40)
    action, adapter, repository = ACTIONS[action_index]
    assert adapter_module.run(
        build_plan(action, adapter, repository), workspace, result_path
    ) == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "resolved source SHA does not match requested source_ref"


def test_cross_bound_plan_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    bad = build_plan(ACTIONS[2][0], ACTIONS[2][1], ACTIONS[2][2])
    bad["target_repo"] = "GlacierEQ/monolith"
    assert casey_legal_mcp_validate.run(bad, workspace, result_path) == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "target_repo identity mismatch"
