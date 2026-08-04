from __future__ import annotations

import json
from pathlib import Path

from scripts import apex_catalog_runner, apex_pillar_runner

SOURCE_SHA = "a" * 40


def plan() -> dict:
    return {
        "job_id": "BoundedMediaJob01",
        "pillar": "A",
        "action": "media-queue",
        "adapter": "media-queue",
        "task": "queue",
        "source_repo": "GlacierEQ/media-workload",
        "source_ref": SOURCE_SHA,
        "target_repo": "GlacierEQ/media-workload",
        "approval_id": "",
        "workflow_run_id": "1",
        "workflow_run_attempt": "1",
        "trigger_actor": "GlacierEQ",
        "trigger_actor_id": "194243768",
        "event_name": "workflow_dispatch",
        "execution_repo": "GlacierEQ/public-actions-runner-host",
        "public_runner_sha": SOURCE_SHA,
    }


def test_media_receipt_is_bounded_and_manifest_is_stable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workload"
    workspace.mkdir()
    for index in range(apex_catalog_runner.MAX_EMBEDDED_RECORDS + 44):
        path = workspace / f"clip-{index:04d}.mp3"
        path.write_bytes(f"media-{index}\n".encode())

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SOURCE_SHA)
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    assert apex_catalog_runner.media_queue(plan(), workspace, first_path) == 0
    assert apex_catalog_runner.media_queue(plan(), workspace, second_path) == 0

    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    expected_count = apex_catalog_runner.MAX_EMBEDDED_RECORDS + 44

    assert first["status"] == "completed"
    assert first["media_count"] == expected_count
    assert first["media_records_included"] == apex_catalog_runner.MAX_EMBEDDED_RECORDS
    assert first["media_records_truncated"] is True
    assert len(first["media"]) == apex_catalog_runner.MAX_EMBEDDED_RECORDS
    assert len(first["media_manifest_sha256"]) == 64
    assert first["media_manifest_sha256"] == second["media_manifest_sha256"]
    assert first["media_total_bytes"] == second["media_total_bytes"]
    assert first_path.stat().st_size < apex_pillar_runner.MAX_RESULT_BYTES
