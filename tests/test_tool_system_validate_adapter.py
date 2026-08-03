from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import action_face_plan as planner

from domains.code.adapters import tool_system_validate as adapter


def production_plan() -> dict:
    return {
        "job_id": "tool-system-test-001",
        "pillar": "C",
        "action": adapter.EXPECTED_ACTION,
        "adapter": adapter.EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": adapter.EXPECTED_REPOSITORY,
        "source_ref": "b105f48cf0dd60f5e376be7b7e807cdb3c4a22c6",
        "target_repo": adapter.EXPECTED_REPOSITORY,
    }


def write_workload(root: Path) -> None:
    for relative in adapter.REQUIRED_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")


def test_command_contract_is_fixed_and_contains_no_shell(tmp_path: Path) -> None:
    commands = adapter.command_sequence(tmp_path / "result.json", "tool-system-test-001")
    assert len(commands) == 6
    assert all(isinstance(command, list) and command for command in commands)
    assert all(command[0] not in {"bash", "sh", "zsh"} for command in commands)
    flattened = [item for command in commands for item in command]
    assert "scripts/ci/verify_tool_system.py" in flattened
    assert "tests/test_tool_system.py" in flattened
    assert set(adapter.LINT_PATHS).issubset(set(flattened))


def test_plan_rebinding_is_blocked_before_execution(tmp_path: Path) -> None:
    plan = production_plan()
    plan["source_repo"] = "GlacierEQ/other-repository"
    result_path = tmp_path / "result.json"
    assert adapter.run(plan, tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert result["reason"] == "source repository identity mismatch"
    assert "steps" not in result


def test_missing_required_files_are_reported_without_execution(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    assert adapter.run(production_plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "config/tool_system.json" in result["reason"]


def test_action_face_planner_binds_canonical_code_action(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")
    plan = planner.build_plan(
        str(event),
        {
            "pillar": "C",
            "action": adapter.EXPECTED_ACTION,
            "job_id": "tool-system-plan-001",
            "source_ref": "b105f48cf0dd60f5e376be7b7e807cdb3c4a22c6",
        },
    )
    assert plan["action"] == adapter.EXPECTED_ACTION
    assert plan["adapter"] == adapter.EXPECTED_ADAPTER
    assert plan["source_repo"] == adapter.EXPECTED_REPOSITORY
    assert plan["target_repo"] == adapter.EXPECTED_REPOSITORY
    assert plan["task"] == "test"
