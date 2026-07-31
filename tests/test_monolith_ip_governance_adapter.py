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
    scan_summary,
)

SOURCE_SHA = "a" * 40


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
    (root / "catalog").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "scripts").mkdir()
    (root / "tests").mkdir()

    manifest = {
        "schemaVersion": "1.1.0",
        "repository": {
            "fullName": "GlacierEQ/monolith",
            "visibility": "private",
        },
        "rightsStatus": "unknown",
        "publication": {"class": "public-orientation-candidate"},
        "releaseApproval": {"status": "blocked"},
        "authorship": {
            "aiAssistance": "material",
            "humanReviewStatus": "in-progress",
        },
    }
    schema = {"type": "object"}
    (root / "ip-manifest.json").write_text(
        json.dumps(manifest) + "\n", encoding="utf-8"
    )
    (root / "catalog" / "rights_overlay.json").write_text(
        json.dumps({"mode": "fail-closed-overlay"}) + "\n", encoding="utf-8"
    )
    (root / "schemas" / "ip-manifest.schema.json").write_text(
        json.dumps(schema) + "\n", encoding="utf-8"
    )
    for name in (
        "json_schema_subset.py",
        "validate_ip_manifest.py",
        "validate_release_evidence.py",
    ):
        (root / "scripts" / name).write_text(
            "raise SystemExit(0)\n", encoding="utf-8"
        )
    (root / "scripts" / "load_governed_catalog.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    (root / "scripts" / "scan_secrets.py").write_text(
        "import argparse, hashlib, json\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--root')\n"
        "p.add_argument('--commit', required=True)\n"
        "p.add_argument('--output', required=True)\n"
        "a=p.parse_args()\n"
        "r={'schemaVersion':'1.1.0','scanner':'fixture-scan','status':'passed',"
        "'scannedCommit':a.commit,'filesTracked':5,'filesScanned':5,"
        "'filesSkipped':0,'findingCount':0,'findings':[]}\n"
        "r['reportSha256']=hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest()\n"
        "open(a.output,'w',encoding='utf-8').write(json.dumps(r))\n",
        encoding="utf-8",
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
    monkeypatch.setenv("APEX_CONTROL_TOKEN", "must-not-pass")
    env = isolated_env()
    assert "UNRELATED_VALUE" not in env
    assert "APEX_CONTROL_TOKEN" not in env
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
            "schemaVersion": "1.1.0",
            "repository": {
                "fullName": "GlacierEQ/monolith",
                "visibility": "private",
                "internal": "not returned",
            },
            "rightsStatus": "unknown",
            "publication": {
                "class": "public-orientation-candidate",
                "notes": "not returned",
            },
            "releaseApproval": {"status": "blocked", "notes": "not returned"},
            "authorship": {
                "aiAssistance": "material",
                "humanReviewStatus": "in-progress",
                "evidence": ["not returned"],
            },
        }
    ) == {
        "schema_version": "1.1.0",
        "repository": "GlacierEQ/monolith",
        "visibility": "private",
        "rights_status": "unknown",
        "publication_class": "public-orientation-candidate",
        "release_status": "blocked",
        "ai_assistance": "material",
        "human_review_status": "in-progress",
    }


def test_scan_summary_is_bounded() -> None:
    assert scan_summary(
        {
            "scanner": "fixture-scan",
            "status": "passed",
            "scannedCommit": SOURCE_SHA,
            "filesTracked": 5,
            "filesScanned": 5,
            "filesSkipped": 0,
            "findingCount": 0,
            "reportSha256": "b" * 64,
            "findings": [{"secret": "not returned"}],
        }
    ) == {
        "scanner": "fixture-scan",
        "status": "passed",
        "scanned_commit": SOURCE_SHA,
        "files_tracked": 5,
        "files_scanned": 5,
        "files_skipped": 0,
        "finding_count": 0,
        "report_sha256": "b" * 64,
    }


def test_adapter_executes_repository_owned_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_fixture(tmp_path)
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    result_path = tmp_path.parent / "result.json"

    assert run(plan(), tmp_path, result_path) == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["resolved_source_sha"] == SOURCE_SHA
    assert result["test_count"] == 1
    assert result["manifest_summary"]["release_status"] == "blocked"
    assert result["secret_scan"]["status"] == "passed"
    assert result["secret_scan"]["scanned_commit"] == SOURCE_SHA
    assert set(result["critical_file_sha256"]) == {
        "ip-manifest.json",
        "catalog/rights_overlay.json",
        "schemas/ip-manifest.schema.json",
        "scripts/json_schema_subset.py",
        "scripts/load_governed_catalog.py",
        "scripts/scan_secrets.py",
        "scripts/validate_ip_manifest.py",
        "scripts/validate_release_evidence.py",
    }


def test_adapter_fails_when_release_evidence_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_fixture(tmp_path)
    (tmp_path / "scripts" / "validate_release_evidence.py").write_text(
        "raise SystemExit(1)\n", encoding="utf-8"
    )
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    result_path = tmp_path.parent / "result-evidence.json"

    assert run(plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["failed_step"] == 4


def test_adapter_rejects_zero_test_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_fixture(tmp_path, include_test=False)
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    result_path = tmp_path.parent / "result-zero.json"

    assert run(plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "zero tests" in result["reason"]


def test_adapter_blocks_missing_source_sha(tmp_path: Path) -> None:
    write_fixture(tmp_path)
    result_path = tmp_path.parent / "result-no-sha.json"

    assert run(plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "APEX_RESOLVED_SOURCE_SHA" in result["reason"]


def test_adapter_blocks_missing_governance_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    result_path = tmp_path.parent / "result-missing.json"

    assert run(plan(), tmp_path, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "ip-manifest.json" in result["reason"]
