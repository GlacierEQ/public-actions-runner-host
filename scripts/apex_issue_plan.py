#!/usr/bin/env python3
"""Normalize a metadata-only APEX job from a public GitHub issue."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import apex_pillar_runner as runner


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = event.get("issue") or {}
    title = str(issue.get("title", ""))
    if not title.startswith("[APEX JOB] "):
        runner.fail("issue is not an APEX job")
    payload = json.loads(str(issue.get("body", "")))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        empty_event = handle.name
    try:
        plan = runner.load_plan(empty_event, payload)
    finally:
        Path(empty_event).unlink(missing_ok=True)
    plan["issue_number"] = str(issue.get("number", ""))
    runner.emit_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
