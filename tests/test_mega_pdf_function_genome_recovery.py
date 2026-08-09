from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from domains.code.adapters import mega_pdf_function_genome as recovery
from scripts import action_face_plan


def _receipt(previous: str | None = None) -> dict:
    payload = {
        "connector": "MEGA-PDF",
        "function": "demo.py::read_demo",
        "success": True,
        "started_at": "2026-08-09T00:00:00+00:00",
        "completed_at": "2026-08-09T00:00:00+00:00",
        "latency_ms": 0,
        "result_digest": "1" * 64,
        "previous_receipt_hash": previous,
        "metadata": {
            "probe_level": "static",
            "imports_executed": False,
            "source_executed": False,
        },
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload | {"receipt_hash": digest}


def _write_valid_outputs(root: Path) -> dict:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    receipt = _receipt()
    report = {
        "schema_version": recovery.EXPECTED_SCHEMA,
        "inventory_digest": "2" * 64,
        "receipt_root": receipt["receipt_hash"],
        "receipt_chain_valid": True,
        "parse_failures": [],
        "scan_issues": [],
        "summary": {
            "discovered": 1,
            "promoted_to_probed": 1,
            "blocked": 0,
            "approved": 0,
            "defaults_promoted": 0,
            "receipts": 1,
        },
    }
    (artifacts / "function-genome.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (artifacts / "probe-receipts.jsonl").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (artifacts / "function-genome-summary.md").write_text(
        "# Function Genome\n", encoding="utf-8"
    )
    return report


def test_recovery_plan_is_exact_sha_and_catalog_bound(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text('{"action":""}\n', encoding="utf-8")
    plan = action_face_plan.build_plan(
        str(event),
        {
            "pillar": "C",
            "job_id": "MegaRecovery01",
            "action": recovery.EXPECTED_ACTION,
            "source_ref": "a" * 40,
        },
    )
    assert plan["source_repo"] == recovery.EXPECTED_REPOSITORY
    assert plan["target_repo"] == recovery.EXPECTED_REPOSITORY
    assert plan["adapter"] == recovery.EXPECTED_ADAPTER
    assert plan["task"] == "test"

    with pytest.raises(SystemExit, match="requires a full lowercase commit SHA"):
        action_face_plan.build_plan(
            str(event),
            {
                "pillar": "C",
                "job_id": "MegaRecovery02",
                "action": recovery.EXPECTED_ACTION,
                "source_ref": "main",
            },
        )


def test_recovery_adapter_rejects_identity_drift() -> None:
    plan = {
        "pillar": "C",
        "action": recovery.EXPECTED_ACTION,
        "adapter": recovery.EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": recovery.EXPECTED_REPOSITORY,
        "target_repo": recovery.EXPECTED_REPOSITORY,
    }
    recovery.validate_plan(plan)
    plan["source_repo"] = "GlacierEQ/not-mega-pdf"
    with pytest.raises(ValueError, match="source_repo identity mismatch"):
        recovery.validate_plan(plan)


def test_generated_receipts_are_recomputed_before_payload_return(tmp_path: Path) -> None:
    report = _write_valid_outputs(tmp_path)
    verified = recovery.verify_generated_outputs(tmp_path, "b" * 40)
    assert verified["report"] == report
    relay = verified["relay_receipt"]
    assert relay["source_sha"] == "b" * 40
    assert relay["receipt_root"] == report["receipt_root"]
    assert relay["credential_path"] == "canonical_apex_oidc_read_only"

    payload = recovery.collect_payload(tmp_path, "b" * 40)
    assert payload["status"] == "available"
    assert payload["resolved_source_sha"] == "b" * 40
    assert [item["path"] for item in payload["files"]] == list(recovery.OUTPUTS)
    assert all(len(item["sha256"]) == 64 for item in payload["files"])


def test_tampered_probe_receipt_fails_closed(tmp_path: Path) -> None:
    _write_valid_outputs(tmp_path)
    receipts = tmp_path / "artifacts/probe-receipts.jsonl"
    row = json.loads(receipts.read_text(encoding="utf-8"))
    row["success"] = False
    receipts.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(recovery.FunctionGenomeError, match="hash verification failed"):
        recovery.verify_generated_outputs(tmp_path, "c" * 40)


def test_artifact_total_bound_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_valid_outputs(tmp_path)
    recovery.verify_generated_outputs(tmp_path, "d" * 40)
    monkeypatch.setattr(recovery, "MAX_OUTPUT_TOTAL_BYTES", 32)
    with pytest.raises(recovery.FunctionGenomeError, match="total byte bound"):
        recovery.collect_payload(tmp_path, "d" * 40)
