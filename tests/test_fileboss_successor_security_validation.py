from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dispatcher import domain_registry
from domains.code.adapters import fileboss_security_validate as adapter
import action_face_plan


SHA = "a" * 40


def test_successor_actions_are_closed_read_only_registry_entries() -> None:
    expected = {
        "code.scribe.validate-fileboss-security": "GlacierEQ/scribe-multimodal-master",
        "code.sigma.validate-fileboss-security": "GlacierEQ/sigma-file-manager",
    }
    for action_name, repository in expected.items():
        action = domain_registry.resolve_action(action_name)
        assert action["targetRepository"] == repository
        assert action["adapter"] == "fileboss_security_validate"
        assert action["executionMode"] == "source-read-only"
        profile = action["tokenProfileContract"]
        assert profile["permissions"] == {"contents": "read"}
        assert profile["repositoryCount"] == 1
        assert profile["exposeCredentialToWorkload"] is False
        assert profile["sourceWrites"] == "forbidden"


def test_action_face_plans_each_successor_at_exact_sha(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    for action_name, repository in (
        ("code.scribe.validate-fileboss-security", "GlacierEQ/scribe-multimodal-master"),
        ("code.sigma.validate-fileboss-security", "GlacierEQ/sigma-file-manager"),
    ):
        plan = action_face_plan.build_plan(
            str(event),
            {"job_id": "SecurityJob01", "pillar": "C", "action": action_name, "source_ref": SHA},
        )
        assert plan["source_repo"] == repository
        assert plan["target_repo"] == repository
        assert plan["source_ref"] == SHA
        assert plan["task"] == "test"
        assert plan["adapter"] == "fileboss_security_validate"


def test_action_face_rejects_mutable_successor_ref(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="requires a full lowercase commit SHA"):
        action_face_plan.build_plan(
            str(event),
            {
                "job_id": "SecurityJob01",
                "pillar": "C",
                "action": "code.scribe.validate-fileboss-security",
                "source_ref": "main",
            },
        )


def test_adapter_rejects_cross_repository_plan() -> None:
    plan = {
        "job_id": "SecurityJob01",
        "pillar": "C",
        "action": "code.scribe.validate-fileboss-security",
        "adapter": "fileboss_security_validate",
        "task": "test",
        "source_repo": "GlacierEQ/sigma-file-manager",
        "source_ref": SHA,
        "target_repo": "GlacierEQ/sigma-file-manager",
    }
    with pytest.raises(ValueError, match="source_repo identity mismatch"):
        adapter.validate_plan(plan)


def test_adapter_command_contract_is_fixed_and_non_shell() -> None:
    scribe = adapter.ACTION_SPECS["code.scribe.validate-fileboss-security"]
    sigma = adapter.ACTION_SPECS["code.sigma.validate-fileboss-security"]
    assert len(scribe["commands"]) == 3
    assert len(sigma["commands"]) == 2
    assert all(command[0] == sys.executable for command in scribe["commands"])
    assert all(command[0] == sys.executable for command in sigma["commands"])
    assert any("test_upload_stream_bounds.py" in command for command in scribe["commands"])
    assert any("test_ide_git_path_boundaries.py" in command for command in sigma["commands"])


def test_result_schemas_are_closed_and_action_specific() -> None:
    for name, action_name in (
        ("scribe-fileboss-security-result.schema.json", "code.scribe.validate-fileboss-security"),
        ("sigma-fileboss-security-result.schema.json", "code.sigma.validate-fileboss-security"),
    ):
        schema = json.loads((ROOT / "domains" / "code" / "schemas" / name).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
        assert schema["properties"]["action"]["const"] == action_name
        assert schema["properties"]["source_ref"]["pattern"] == "^[0-9a-f]{40}$"
