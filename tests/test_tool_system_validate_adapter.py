from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import action_face_plan as planner

from domains.code.adapters import tool_system_validate as adapter

SOURCE_SHA = "a" * 40


def production_plan() -> dict:
    return {
        "job_id": "tool-system-test-001",
        "pillar": "C",
        "action": adapter.EXPECTED_ACTION,
        "adapter": adapter.EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": adapter.EXPECTED_REPOSITORY,
        "source_ref": SOURCE_SHA,
        "target_repo": adapter.EXPECTED_REPOSITORY,
    }


def canonical_plan() -> dict:
    return {
        "job_id": "tool-system-canonical-001",
        "domain": "code",
        "action": adapter.EXPECTED_ACTION,
        "source_ref": SOURCE_SHA,
        "expected_source_sha": SOURCE_SHA,
    }


def write_paths(root: Path, paths: tuple[str, ...]) -> None:
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")


def write_workload(root: Path) -> None:
    write_paths(root, adapter.REQUIRED_PATHS)


def write_bounded_workload(root: Path) -> None:
    write_paths(root, adapter.BOUNDED_REQUIRED_PATHS)


def write_kernel_workload(root: Path) -> None:
    write_paths(root, adapter.KERNEL_REQUIRED_PATHS)


def test_command_contract_is_fixed_and_contains_no_shell(tmp_path: Path) -> None:
    commands = adapter.command_sequence(
        tmp_path / "result.json", "tool-system-test-001"
    )
    assert len(commands) == 6
    assert all(isinstance(command, list) and command for command in commands)
    assert all(command[0] not in {"bash", "sh", "zsh"} for command in commands)
    flattened = [item for command in commands for item in command]
    assert "scripts/ci/verify_tool_system.py" in flattened
    assert "tests/test_tool_system.py" in flattened
    assert set(adapter.LINT_PATHS).issubset(set(flattened))
    with pytest.raises(ValueError, match="job_id is invalid"):
        adapter.command_sequence(tmp_path / "result.json", "../../unsafe")


def test_kernel_command_delegates_only_to_repository_owned_gate(tmp_path: Path) -> None:
    commands = adapter.command_sequence(
        tmp_path / "result.json",
        "computer-kernel-test-001",
        "computer-kernel-v1",
    )
    assert len(commands) == 3
    assert commands[-1] == ["bash", "scripts/ci/kernel_verify.sh"]
    assert all(isinstance(command, list) and command for command in commands)
    assert all("../" not in item for command in commands for item in command)


def test_isolated_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("AKOS_POLICY_SHA256", "secret-digest")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("UNRELATED_SECRET", "secret")
    env = adapter.isolated_env()
    assert env["PATH"] == "/usr/bin"
    assert env["CI"] == "true"
    assert "AKOS_POLICY_SHA256" not in env
    assert "GITHUB_TOKEN" not in env
    assert "UNRELATED_SECRET" not in env


def test_canonical_job_normalizes_to_fixed_execution_identity() -> None:
    normalized = adapter.normalize_plan(canonical_plan())
    assert normalized["pillar"] == "C"
    assert normalized["adapter"] == adapter.EXPECTED_ADAPTER
    assert normalized["source_repo"] == adapter.EXPECTED_REPOSITORY
    assert normalized["target_repo"] == adapter.EXPECTED_REPOSITORY
    assert normalized["expected_source_sha"] == SOURCE_SHA


def test_plan_rebinding_is_blocked_before_execution(tmp_path: Path) -> None:
    plan = production_plan()
    plan["source_repo"] = "GlacierEQ/other-repository"
    result_path = tmp_path / "result.json"
    assert adapter.run(plan, tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "source repo identity mismatch"
    assert "steps" not in result


def test_missing_required_files_are_reported_without_execution(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    assert adapter.run(production_plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "config/tool_system.json" in result["reason"]


def test_complete_kernel_surface_takes_precedence_over_legacy_surfaces(
    tmp_path: Path,
) -> None:
    write_kernel_workload(tmp_path)
    write_workload(tmp_path)
    surface, reason = adapter._surface(tmp_path)
    assert surface == "computer-kernel-v1"
    assert reason is None


def test_complete_legacy_only_workload_keeps_legacy_surface(tmp_path: Path) -> None:
    write_workload(tmp_path)
    surface, reason = adapter._surface(tmp_path)
    assert surface == "tool-system-v2"
    assert reason is None


def test_complete_bounded_only_workload_keeps_bounded_surface(tmp_path: Path) -> None:
    write_bounded_workload(tmp_path)
    surface, reason = adapter._surface(tmp_path)
    assert surface == "bounded-smithery-v7"
    assert reason is None


def test_partial_kernel_surface_fails_closed_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    write_workload(tmp_path)
    first_kernel_path = tmp_path / adapter.KERNEL_INDICATOR_PATHS[0]
    first_kernel_path.parent.mkdir(parents=True, exist_ok=True)
    first_kernel_path.write_text("# partial kernel\n", encoding="utf-8")
    surface, reason = adapter._surface(tmp_path)
    assert surface == "blocked"
    assert reason is not None
    assert "partial computer kernel surface" in reason
    assert adapter.KERNEL_REQUIRED_PATHS[1] in reason


def test_symlinked_kernel_gate_is_rejected_before_execution(tmp_path: Path) -> None:
    for relative in adapter.KERNEL_REQUIRED_PATHS:
        if relative != "scripts/ci/kernel_verify.sh":
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-kernel-verify.sh"
    outside.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    gate = tmp_path / "scripts/ci/kernel_verify.sh"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.symlink_to(outside)

    surface, reason = adapter._surface(tmp_path)
    assert surface == "blocked"
    assert reason is not None
    assert "unsafe computer kernel paths" in reason
    assert "scripts/ci/kernel_verify.sh" in reason


def test_expected_source_mismatch_blocks_before_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_workload(tmp_path)
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "b" * 40)
    result_path = tmp_path / "result.json"
    assert adapter.run(canonical_plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["expected_source_sha"] == SOURCE_SHA
    assert "does not match" in result["reason"]
    assert "steps" not in result


def test_none_stdout_is_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_workload(tmp_path)
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=None),
    )
    result_path = tmp_path / "result.json"
    assert adapter.run(production_plan(), tmp_path, result_path) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert len(result["steps"]) == 6
    assert all(step["output_tail"] == "" for step in result["steps"])


def test_kernel_run_forwards_exact_private_sha_to_repository_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_kernel_workload(tmp_path)
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    observed: list[dict] = []

    def fake_run(command, **kwargs):
        observed.append({"command": command, "env": dict(kwargs["env"])})
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result_path = tmp_path / "result.json"
    assert adapter.run(canonical_plan(), tmp_path, result_path) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert len(result["steps"]) == 3
    assert observed[-1]["command"][0] == "bash"
    assert (
        Path(observed[-1]["command"][1])
        == (tmp_path / "scripts/ci/kernel_verify.sh").resolve()
    )
    assert observed[-1]["env"]["GITHUB_SHA"] == SOURCE_SHA
    assert observed[-1]["env"]["APEX_RESOLVED_SOURCE_SHA"] == SOURCE_SHA
    assert "GITHUB_TOKEN" not in observed[-1]["env"]


def test_action_face_planner_binds_canonical_code_action(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    plan = planner.build_plan(
        str(event),
        {
            "pillar": "C",
            "action": adapter.EXPECTED_ACTION,
            "job_id": "tool-system-plan-001",
            "source_ref": SOURCE_SHA,
        },
    )
    assert plan["action"] == adapter.EXPECTED_ACTION
    assert plan["adapter"] == adapter.EXPECTED_ADAPTER
    assert plan["source_repo"] == adapter.EXPECTED_REPOSITORY
    assert plan["target_repo"] == adapter.EXPECTED_REPOSITORY
    assert plan["task"] == "test"


def test_catalog_runner_direct_entrypoint_imports_domain_adapter() -> None:
    process = subprocess.run(
        [sys.executable, "scripts/action_face_catalog_runner.py"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = process.stdout or ""
    assert process.returncode != 0
    assert "usage: action_face_catalog_runner.py" in output
    assert "ModuleNotFoundError" not in output
