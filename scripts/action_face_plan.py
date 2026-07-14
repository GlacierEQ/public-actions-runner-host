#!/usr/bin/env python3
"""Normalize canonical and migrated events into an APEX public-runner plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import apex_pillar_runner as base

EVENT_PILLARS = {
    **base.PILLARS,
    "media-queue": "A",
    "whisperx-exec": "A",
    "gateway-ci": "F",
    "comet-agent-ci": "C",
    "apex-verification": "C",
}

EVENT_DEFAULT_ACTION = {
    "media-queue": "media-queue",
    "whisperx-exec": "whisperx-exec",
    "gateway-ci": "gateway-ci",
    "comet-agent-ci": "comet-agent-ci",
    "apex-verification": "apex-verification",
}

CATALOGS = [
    Path("config/pillar-actions.json"),
    Path("config/action-face-actions.json"),
]

ADAPTER_TASK = {
    **base.ADAPTER_TASK,
    "apex-verify": "validate",
    "python-ci": "test",
    "node-ci": "test",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def catalog_actions() -> list[dict]:
    actions: list[dict] = []
    for path in CATALOGS:
        data = json.loads(path.read_text(encoding="utf-8"))
        actions.extend(data.get("actions", []))
    return actions


def resolve_action(action: str, pillar: str) -> dict | None:
    if not action:
        return None
    matches = [item for item in catalog_actions() if item.get("action") == action and item.get("pillar") == pillar]
    if len(matches) != 1:
        fail("action is not registered to the requested pillar")
    return matches[0]


def build_plan(event_path: str, manual: dict[str, str]) -> dict:
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    event_name = str(event.get("action", ""))

    if event_name in EVENT_PILLARS:
        payload = dict(event.get("client_payload") or {})
        pillar = EVENT_PILLARS[event_name]
        payload.setdefault("action", EVENT_DEFAULT_ACTION.get(event_name, ""))
    else:
        payload = dict(manual)
        pillar = str(payload.get("pillar", "")).upper()

    entry = resolve_action(str(payload.get("action", "")), pillar)
    plan = {
        "job_id": str(payload.get("job_id", "")),
        "pillar": pillar,
        "source_repo": entry["target_repo"] if entry else str(payload.get("source_repo") or "GlacierEQ/public-actions-runner-host"),
        "source_ref": str(payload.get("source_ref") or "main"),
        "task": ADAPTER_TASK[entry["adapter"]] if entry else str(payload.get("task") or "validate"),
        "approval_id": str(payload.get("approval_id", "")),
        "action": entry["action"] if entry else "",
        "adapter": entry["adapter"] if entry else "",
        "target_repo": entry["target_repo"] if entry else "",
    }

    if not base.JOB_ID.fullmatch(plan["job_id"]):
        fail("job_id must be 8-64 safe characters")
    if plan["pillar"] not in base.ALLOWED_TASKS:
        fail("unknown pillar")
    if plan["task"] not in base.ALLOWED_TASKS[plan["pillar"]]:
        fail("task is not allowed for this pillar")
    if not base.REPO.fullmatch(plan["source_repo"]):
        fail("source_repo must be a GlacierEQ repository")
    if not base.REF.fullmatch(plan["source_ref"]) or ".." in plan["source_ref"]:
        fail("invalid source_ref")
    if plan["pillar"] in {"G", "I"} and not base.JOB_ID.fullmatch(plan["approval_id"]):
        fail("pillars G and I require a valid private approval_id")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    for name in ("pillar", "job-id", "source-repo", "source-ref", "task", "approval-id", "action"):
        parser.add_argument(f"--{name}", default="")
    args = parser.parse_args()

    manual = {
        "pillar": args.pillar,
        "job_id": args.job_id,
        "source_repo": args.source_repo,
        "source_ref": args.source_ref,
        "task": args.task,
        "approval_id": args.approval_id,
        "action": args.action,
    }
    base.emit_outputs(build_plan(args.event, manual))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
