from __future__ import annotations

import hashlib
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
