#!/usr/bin/env python3
"""Normalize one metadata-only APEX job file from a public queue commit."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import apex_pillar_runner as runner


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    changed = set()
    commit = event.get("head_commit") or {}
    for key in ("added", "modified"):
        changed.update(commit.get(key) or [])
    for item in event.get("commits") or []:
        for key in ("added", "modified"):
            changed.update(item.get(key) or [])

    jobs = sorted(path for path in changed if path.startswith("jobs/") and path.endswith(".json"))
    if len(jobs) != 1:
        runner.fail("a queue commit must add or modify exactly one jobs/*.json file")

    payload = json.loads(Path(jobs[0]).read_text())
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        empty_event = handle.name
    try:
        plan = runner.load_plan(empty_event, payload)
    finally:
        Path(empty_event).unlink(missing_ok=True)
    runner.emit_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
