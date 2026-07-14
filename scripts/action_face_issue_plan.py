#!/usr/bin/env python3
"""Normalize a metadata-only public issue into the canonical action-face plan."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import action_face_plan as planner
import apex_pillar_runner as base


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    if not str(issue.get("title", "")).startswith("[APEX JOB] "):
        base.fail("issue is not an APEX job")
    payload = json.loads(str(issue.get("body", "")))

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        empty_event = handle.name
    try:
        plan = planner.build_plan(empty_event, payload)
    finally:
        Path(empty_event).unlink(missing_ok=True)

    plan["issue_number"] = str(issue.get("number", ""))
    base.emit_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
