from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import action_face_plan as planner
from monolith_ip_governance_adapter import (
    isolated_env,
    manifest_summary,
    parse_test_count,
    run,
)


def plan() -> dict:
    return {
        "job_id": "monolith-ip-governance-test",
        "pillar": "D",
        "action": "monolith-ip-governance",
        "adapter": "monolith-ip-governance",
        "task": "test",
        "source_repo": "GlacierEQ/monolith",
        "source_ref": "overhaul/ip-control-plane-v1",
        "target_repo": "GlacierEQ/monolith",
    }


def write_fixture(root: Path, *, include_test: bool = True) -> None:
    (root / "schemas").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "tests").mkdir()

    manifest = {
        "schemaVersion": "1.0",
        "repository": "GlacierEQ/monolith",
        "rights": {"status": "unknown"},
        "publication": {"authorization": "blocked"},
    }
    schema = {"type": "object"}
    (root / "ip-manifest.json").write_text(
        json.dumps(manifest) + "\n", encoding="utf-8"
    )
    (root / "schemas" / "ip-manifest.schema.json").write_text(
        json.dumps(schema) + "\n", encoding="utf-8"
    )
    (root / "scripts" / "validate_ip_manifest.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    if include_test:
        (root / "tests" / "test_fixture.py").write_text(
            "import unittest\n"
            "class FixtureTest(unittest.TestCase):\n"
            "    def test_gate(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )


def test_action_is_narrowly_catalogued() -> None:
    entry = planner.resolve_action("monolith-ip-governance", "D")
    assert entry is not None
    assert entry["target_repo"] == "GlacierEQ/monolith"
    assert entry["adapter"] == "monolith-ip-governance"
    assert planner.ADAPTER_TASK["monolith-ip-governance"] == "test"


def test_subprocess_environment_is_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNRELATED_VALUE", "must-not-pass")
    env = isolated_env()
    assert "UNRELATED_VALUE" not in env
    assert env["CI"] == "true"


def test_parse_test_count_rejects_missing_or_zero_tests() -> None:
    with pytest.raises(ValueError, match="did not report"):
        parse_test_count("OK")
    with pytest.raises(ValueError, match="zero tests"):
        parse_test_count("Ran 0 tests in 0.000s\nOK")
    assert parse_test_count("Ran 19 tests in 0.100s\nOK") == 19


def test_manifest_summary_is_bounded() -> None:
    assert manifest_summary(
        {
            "schemaVersion": "1.0",
            "repository": "GlacierEQ/monolith",
            "rights": {"status": "unknown", "internal": "not returned"},
            "publication": {"authorization": "blocked", "notes": "not returned"},
        }
    ) == {
        "schema_version": "1.0",
        "repository": "GlacierEQ/monolith",
        "rights_status": "unknown",
        "publication_authorization": "blocked",
    }


def test_adapter_executes_repository_owned_gate(tmp_path: Path) -> None:
    write_fixture(tmp_path)
    result_path = tmp_path.parent / "result.json"

    assert run(plan(), tmp_path, result_path) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["test_count"] == 1
    assert result["manifest_summary"]["publication_authorization"] == "blocked"
    assert set(result["critical_file_sha256"]) == {
        "ip-manifest.json",
        "schemas/ip-manifest.schema.json",
        "scripts/validate_ip_manifest.py",
    }


def test_adapter_rejects_zero_test_execution(tmp_path: Path) -> None:
    write_fixture(tmp_path, include_test=False)
    result_path = tmp_path.parent / "result-zero.json"

    assert run(plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "zero tests" in result["reason"]


def test_adapter_blocks_missing_governance_paths(tmp_path: Path) -> None:
    result_path = tmp_path.parent / "result-missing.json"

    assert run(plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "ip-manifest.json" in result["reason"]
