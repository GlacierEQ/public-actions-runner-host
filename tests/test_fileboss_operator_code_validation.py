from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dispatcher import domain_registry
from domains.code.adapters import fileboss_operator_code_validate
from scripts import action_face_plan, apex_catalog_runner

ACTION = "code.fileboss.validate-operator-code-bridge"
ADAPTER = "fileboss_operator_code_validate"
REPOSITORY = "GlacierEQ/FILEBOSS"
SHA = "a" * 40
ATTESTATION = {
    "resolved_source_sha": SHA,
    "tracked_clean": True,
    "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}


def build_plan() -> dict:
    return {
        "job_id": "Operator01",
        "pillar": "C",
        "action": ACTION,
        "adapter": ADAPTER,
        "task": "test",
        "source_repo": REPOSITORY,
        "source_ref": SHA,
        "target_repo": REPOSITORY,
        "approval_id": "",
        "workflow_run_id": "1",
        "workflow_run_attempt": "1",
        "trigger_actor": "GlacierEQ",
        "trigger_actor_id": "1",
        "event_name": "workflow_dispatch",
        "execution_repo": "GlacierEQ/public-actions-runner-host",
        "public_runner_sha": SHA,
    }


def write_required_files(workspace: Path) -> None:
    for relative in fileboss_operator_code_validate.REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            content = "{}\n"
        elif path.suffix == ".py":
            content = "pass\n"
        else:
            content = "# fixture\n"
        path.write_text(content, encoding="utf-8")


def assert_process_contract(kwargs: dict, workspace: Path) -> None:
    assert kwargs["cwd"] == workspace
    assert kwargs["text"] is True
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None
    assert kwargs["timeout"] == 900
    assert kwargs["check"] is False
    assert kwargs["shell"] is False
    assert kwargs["env"] == {}


def test_registry_contract_is_exact_and_read_only() -> None:
    contract = domain_registry.resolve_action(ACTION, root=ROOT)
    assert contract["adapter"] == ADAPTER
    assert contract["targetRepository"] == REPOSITORY
    assert contract["executionMode"] == "source-read-only"
    profile = contract["tokenProfileContract"]
    assert profile["permissions"] == {"contents": "read"}
    assert profile["repositoryCount"] == 1
    assert profile["persistCredentials"] is False
    assert profile["exposeCredentialToWorkload"] is False
    assert profile["sourceWrites"] == "forbidden"


def test_action_face_builds_exact_immutable_plan(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    result = action_face_plan.build_plan(
        str(event),
        {
            "job_id": "Operator01",
            "pillar": "C",
            "action": ACTION,
            "source_ref": SHA,
        },
    )
    assert result["action"] == ACTION
    assert result["adapter"] == "fileboss-operator-code-validate"
    assert result["source_repo"] == REPOSITORY
    assert result["target_repo"] == REPOSITORY
    assert result["source_ref"] == SHA
    assert result["task"] == "test"


def test_action_face_rejects_mutable_operator_source(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="full lowercase commit SHA"):
        action_face_plan.build_plan(
            str(event),
            {
                "job_id": "Operator01",
                "pillar": "C",
                "action": ACTION,
                "source_ref": "main",
            },
        )


def test_catalog_dispatch_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[dict, Path, Path]] = []

    def fake_run(plan, workspace, result_path):
        calls.append((plan, workspace, result_path))
        return 0

    monkeypatch.setattr(fileboss_operator_code_validate, "run", fake_run)
    plan = build_plan()
    workspace = Path("workspace")
    result_path = Path("result.json")
    assert apex_catalog_runner.run_registered_specialization(
        plan, workspace, result_path
    ) == 0
    assert calls == [(plan, workspace, result_path)]


def test_adapter_runs_only_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_required_files(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        fileboss_operator_code_validate,
        "attest_workspace",
        lambda *_: dict(ATTESTATION),
    )
    monkeypatch.setattr(
        fileboss_operator_code_validate,
        "build_environment",
        lambda *_: {},
    )
    expected = fileboss_operator_code_validate.commands()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        assert command == expected[len(calls)]
        assert_process_contract(kwargs, workspace)
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr(fileboss_operator_code_validate.subprocess, "run", fake_run)
    assert fileboss_operator_code_validate.run(
        build_plan(), workspace, result_path
    ) == 0
    assert calls == expected
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["validated_gates"] == [
        "operator-code-job-schema",
        "operator-code-python-syntax",
        "operator-code-security-tests",
    ]
    assert len(result["steps"]) == 3


def test_adapter_failure_stops_and_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_required_files(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        fileboss_operator_code_validate,
        "attest_workspace",
        lambda *_: dict(ATTESTATION),
    )
    monkeypatch.setattr(
        fileboss_operator_code_validate,
        "build_environment",
        lambda *_: {},
    )
    expected = fileboss_operator_code_validate.commands()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        assert command == expected[0]
        assert_process_contract(kwargs, workspace)
        calls.append(command)
        return SimpleNamespace(returncode=9, stdout="schema failed\n")

    monkeypatch.setattr(fileboss_operator_code_validate.subprocess, "run", fake_run)
    assert fileboss_operator_code_validate.run(
        build_plan(), workspace, result_path
    ) == 2
    assert calls == expected[:1]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["steps"][0]["exit_code"] == 9
    assert result["steps"][0]["status"] == "failed"
    assert result["steps"][0]["output_tail"] == "schema failed\n"


def test_adapter_blocks_resolved_sha_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "b" * 40)
    assert fileboss_operator_code_validate.run(
        build_plan(), workspace, result_path
    ) == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "resolved source SHA does not match requested source_ref"


def test_adapter_blocks_cross_repository_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    plan = build_plan()
    plan["target_repo"] = "GlacierEQ/monolith"
    assert fileboss_operator_code_validate.run(
        plan, workspace, result_path
    ) == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "target_repo identity mismatch"
