#!/usr/bin/env python3
"""Normalize one metadata-only public queue job through the action-face planner."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import action_face_plan as planner
import apex_pillar_runner as base


def changed_paths(event: dict) -> set[str]:
    changed: set[str] = set()
    commit = event.get("head_commit") or {}
    for key in ("added", "modified"):
        changed.update(commit.get(key) or [])
    for item in event.get("commits") or []:
        for key in ("added", "modified"):
            changed.update(item.get(key) or [])

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
    jobs = sorted(path for path in changed_paths(event) if path.startswith("jobs/") and path.endswith(".json"))
    if len(jobs) != 1:
        base.fail("a queue commit must identify exactly one jobs/*.json file")

    payload = json.loads(Path(jobs[0]).read_text(encoding="utf-8"))
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
