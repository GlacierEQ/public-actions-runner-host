from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts import action_face_publish_verified


def invoke_publish(
    monkeypatch: pytest.MonkeyPatch,
    runner: Path,
    result_path: Path,
    expected_digest: str,
) -> tuple[int, list[bytes]]:
    published: list[bytes] = []

    def capture(_job_id: str, path: Path) -> None:
        published.append(path.read_bytes())

    monkeypatch.chdir(runner)
    monkeypatch.setattr(action_face_publish_verified.base, "publish", capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "action_face_publish_verified.py",
            "--job-id",
            "PublishBoundaryJob01",
            "--result",
            str(result_path),
            "--expected-file-sha256",
            expected_digest,
        ],
    )
    return action_face_publish_verified.main(), published


def recovery_result(path: str = "artifacts/proof.json") -> bytes:
    content = '{"ok":true}\n'
    payload = content.encode("utf-8")
    result = {
        "job_id": "PublishBoundaryJob01",
        "resolved_source_sha": "a" * 40,
        "recovery_artifacts": {
            "status": "available",
            "resolved_source_sha": "a" * 40,
            "total_bytes": len(payload),
            "files": [
                {
                    "path": path,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "content": content,
                }
            ],
        },
    }
    return json.dumps(result).encode("utf-8")


def test_verified_regular_result_publishes_exact_locked_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "runner"
    results = runner / ".apex-results"
    results.mkdir(parents=True)
    result_path = results / "PublishBoundaryJob01.json"
    raw = b'{"status":"completed"}\n'
    result_path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    value, published = invoke_publish(
        monkeypatch,
        runner,
        result_path,
        digest,
    )

    assert value == 0
    assert published == [raw]


def test_verified_result_symlink_is_rejected_without_reading_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "runner"
    results = runner / ".apex-results"
    results.mkdir(parents=True)
    outside = tmp_path / "outside-secret.json"
    outside.write_bytes(b'{"secret":"must-not-publish"}\n')
    result_path = results / "PublishBoundaryJob01.json"
    result_path.symlink_to(outside)

    with pytest.raises(SystemExit, match="without following symlinks"):
        invoke_publish(
            monkeypatch,
            runner,
            result_path,
            hashlib.sha256(outside.read_bytes()).hexdigest(),
        )


def test_verified_result_changed_after_guard_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tmp_path / "runner"
    results = runner / ".apex-results"
    results.mkdir(parents=True)
    result_path = results / "PublishBoundaryJob01.json"
    locked = b'{"status":"completed"}\n'
    changed = b'{"status":"failed"}\n'
    result_path.write_bytes(changed)

    with pytest.raises(SystemExit, match="result bytes changed"):
        invoke_publish(
            monkeypatch,
            runner,
            result_path,
            hashlib.sha256(locked).hexdigest(),
        )


def test_recovery_artifacts_are_reverified_and_materialized_under_job_namespace() -> (
    None
):
    records = action_face_publish_verified.recovery_records(
        "PublishBoundaryJob01", recovery_result()
    )
    assert records[0][0] == (
        "recovery-artifacts/PublishBoundaryJob01/artifacts/proof.json"
    )
    assert records[0][1] == b'{"ok":true}\n'
    assert records[1][0] == "recovery-artifacts/PublishBoundaryJob01/manifest.json"
    manifest = json.loads(records[1][1])
    assert manifest["resolved_source_sha"] == "a" * 40
    assert manifest["files"][0]["sha256"] == hashlib.sha256(records[0][1]).hexdigest()


def test_recovery_artifact_path_traversal_fails_closed() -> None:
    raw = recovery_result("artifacts/../secret.json")
    with pytest.raises(SystemExit, match="bounded artifact namespace"):
        action_face_publish_verified.recovery_records("PublishBoundaryJob01", raw)


def test_recovery_artifact_digest_tamper_fails_closed() -> None:
    result = json.loads(recovery_result())
    result["recovery_artifacts"]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="digest does not match content"):
        action_face_publish_verified.recovery_records(
            "PublishBoundaryJob01", json.dumps(result).encode("utf-8")
        )


def test_recovery_materialization_is_idempotent_for_exact_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = action_face_publish_verified.recovery_records(
        "PublishBoundaryJob01", recovery_result()
    )
    stored: dict[str, bytes] = {}

    def fake_api(
        path: str,
        _token: str,
        method: str = "GET",
        payload: dict | None = None,
        *,
        allow_not_found: bool = False,
    ) -> dict | None:
        if method == "GET":
            if path not in stored:
                assert allow_not_found
                return None
            return {"content": base64.b64encode(stored[path]).decode("ascii")}
        assert method == "PUT"
        assert payload is not None
        stored[path] = base64.b64decode(payload["content"])
        return {"content": payload["content"]}

    monkeypatch.setattr(
        action_face_publish_verified.base, "control_token", lambda: "token"
    )
    monkeypatch.setattr(action_face_publish_verified.base, "api", fake_api)
    action_face_publish_verified.publish_recovery_records(
        "PublishBoundaryJob01", records
    )
    first = dict(stored)
    action_face_publish_verified.publish_recovery_records(
        "PublishBoundaryJob01", records
    )
    assert stored == first
