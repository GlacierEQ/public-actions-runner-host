#!/usr/bin/env python3
"""Normalize a bounded metadata-only public issue into the canonical action-face plan."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import action_face_plan as planner
import apex_pillar_runner as base

MAX_BODY_BYTES = 4096
TITLE = re.compile(r"^\[APEX JOB\] ([A-Za-z0-9][A-Za-z0-9._-]{7,63})$")


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    title = str(issue.get("title", ""))
    match = TITLE.fullmatch(title)
    if not match:
        base.fail("issue title must be exactly [APEX JOB] <job_id>")
    job_id_from_title = match.group(1)

    body = str(issue.get("body", ""))
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        base.fail(f"public issue job envelope exceeds {MAX_BODY_BYTES} bytes")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        base.fail(f"public issue body is not valid JSON: line {exc.lineno} column {exc.colno}")
    if not isinstance(payload, dict):
        base.fail("public issue body must contain a JSON object")
    if payload.get("job_id") != job_id_from_title:
        base.fail("issue title job ID must match the envelope job_id")

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
