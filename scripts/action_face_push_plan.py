#!/usr/bin/env python3
"""Normalize one pure, bounded metadata-only public queue job."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import action_face_plan as planner
import apex_pillar_runner as base

JOB_PATH = re.compile(r"^jobs/([A-Za-z0-9][A-Za-z0-9._-]{7,63})\.json$")
MAX_JOB_BYTES = 4096


def changed_paths(event: dict) -> set[str]:
    changed: set[str] = set()
    commit = event.get("head_commit") or {}
    for key in ("added", "modified", "removed"):
        changed.update(str(path) for path in (commit.get(key) or []))
    for item in event.get("commits") or []:
        for key in ("added", "modified", "removed"):
            changed.update(str(path) for path in (item.get(key) or []))

    message = str(commit.get("message", ""))
    match = re.search(r"^dispatch: ([A-Za-z0-9][A-Za-z0-9._-]{7,63})\b", message)
    if match:
        candidate = f"jobs/{match.group(1)}.json"
        if Path(candidate).is_file():
            changed.add(candidate)

    if not changed:
        output = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            text=True,
        )
        changed.update(line.strip() for line in output.splitlines() if line.strip())
    return changed


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    changed = changed_paths(event)
    if len(changed) != 1:
        base.fail("a queue commit must change exactly one file and no unrelated paths")

    relative = next(iter(changed))
    match = JOB_PATH.fullmatch(relative)
    if not match:
        base.fail("the only changed path must be jobs/<job_id>.json")
    job_id_from_path = match.group(1)

    path = Path(relative)
    jobs_root = Path("jobs").resolve()
    resolved = path.resolve()
    if path.is_symlink() or resolved.parent != jobs_root:
        base.fail("queue job path is not a regular file directly under jobs/")
    if not resolved.is_file():
        base.fail("queue job file does not exist or was removed")
    if resolved.stat().st_size > MAX_JOB_BYTES:
        base.fail(f"queue job envelope exceeds {MAX_JOB_BYTES} bytes")

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        base.fail(f"queue job file is not valid JSON: line {exc.lineno} column {exc.colno}")
    if isinstance(payload, dict) and payload.get("job_id") != job_id_from_path:
        base.fail("queue job_id must match the jobs/<job_id>.json filename")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        empty_event = handle.name
    try:
        plan = planner.build_plan(empty_event, payload)
    finally:
        Path(empty_event).unlink(missing_ok=True)

    base.emit_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
