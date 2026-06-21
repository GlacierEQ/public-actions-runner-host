#!/usr/bin/env python3
"""Normalize a metadata-only APEX job from a public GitHub issue."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import apex_pillar_runner as runner

CATALOG = Path("config/pillar-actions.json")
ADAPTER_TASK = {
    "hash-manifest": "hash-manifest",
    "validate": "validate",
    "test": "test",
    "audit": "audit",
    "document-validate": "validate",
    "latex": "validate",
    "pdf-analyze": "validate",
    "notion-sync": "validate",
    "media-queue": "validate",
    "whisperx": "validate",
    "railway": "validate",
    "xcode": "validate",
    "browser-scan": "validate",
    "health-check": "validate",
}


def resolve_action(payload: dict) -> tuple[dict, dict | None]:
    name = str(payload.get("action", ""))
    if not name:
        return payload, None
    catalog = json.loads(CATALOG.read_text())
    matches = [
        item for item in catalog["actions"]
        if item["action"] == name and item["pillar"] == str(payload.get("pillar", "")).upper()
    ]
    if len(matches) != 1:
        runner.fail("action is not registered to the requested pillar")
    entry = matches[0]
    resolved = dict(payload)
    resolved["task"] = ADAPTER_TASK[entry["adapter"]]
    resolved.setdefault("source_repo", entry["target_repo"])
    resolved.setdefault("source_ref", "main")
    return resolved, entry


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = event.get("issue") or {}
    if not str(issue.get("title", "")).startswith("[APEX JOB] "):
        runner.fail("issue is not an APEX job")
    payload, entry = resolve_action(json.loads(str(issue.get("body", ""))))
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        empty_event = handle.name
    try:
        plan = runner.load_plan(empty_event, payload)
    finally:
        Path(empty_event).unlink(missing_ok=True)
    plan["issue_number"] = str(issue.get("number", ""))
    if entry:
        plan["action"] = entry["action"]
        plan["adapter"] = entry["adapter"]
        plan["target_repo"] = entry["target_repo"]
    runner.emit_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
