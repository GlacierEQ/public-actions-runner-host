#!/usr/bin/env python3
"""Normalize and strictly validate public action-face job envelopes."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import apex_pillar_runner as base

EVENT_PILLARS = {
    **base.PILLARS,
    "media-queue": "A",
    "whisperx-exec": "A",
    "gateway-ci": "F",
    "comet-agent-ci": "C",
    "apex-verification": "C",
    "action-face-canary": "F",
}

EVENT_DEFAULT_ACTION = {
    "media-queue": "media-queue",
    "whisperx-exec": "whisperx-exec",
    "gateway-ci": "gateway-ci",
    "comet-agent-ci": "comet-agent-ci",
    "apex-verification": "apex-verification",
    "action-face-canary": "action-face-canary",
}

CATALOGS = [Path("config/pillar-actions.json"), Path("config/action-face-actions.json")]
ADAPTER_TASK = {
    **base.ADAPTER_TASK,
    "apex-verify": "validate",
    "python-ci": "test",
    "node-ci": "test",
    "akos-echo-policy-ci": "test",
    "tool-system-validate": "test",
    "monolith-evolution": "test",
    "monolith-ip-governance": "test",
    "action-face-selftest": "validate",
    "master-strand-inventory": "audit",
    "master-strand-extinction": "audit",
}
ALLOWED_KEYS = {"job_id", "pillar", "action", "source_repo", "source_ref", "task", "approval_id"}
MAX_ENVELOPE_BYTES = 4096
MAX_LENGTH = {
    "job_id": 64,
    "pillar": 1,
    "action": 64,
    "source_repo": 128,
    "source_ref": 128,
    "task": 32,
    "approval_id": 64,
}
ACTION = re.compile(
    r"^(?:[a-z0-9][a-z0-9-]{0,63}|"
    r"[a-z][a-z0-9-]{1,31}(?:\.[a-z][a-z0-9-]{0,63})+)$"
)
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def fail(message: str) -> None:
    raise SystemExit(message)


def catalog_actions() -> list[dict]:
    actions: list[dict] = []
    for path in CATALOGS:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
            fail(f"action catalog {path} is malformed")
        actions.extend(data["actions"])
    return actions


def approved_workloads() -> set[str]:
    return {"GlacierEQ/public-actions-runner-host", *(str(item.get("target_repo", "")) for item in catalog_actions())}


def validate_shape(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        fail("job envelope must be a JSON object")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_ENVELOPE_BYTES:
        fail(f"job envelope exceeds {MAX_ENVELOPE_BYTES} bytes")
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        fail(f"job envelope contains unknown fields: {', '.join(unknown)}")

    normalized: dict[str, str] = {}
    for key, value in payload.items():
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            fail(f"job envelope field {key} must be a string")
        if len(value) > MAX_LENGTH[key]:
            fail(f"job envelope field {key} exceeds its length limit")
        if CONTROL.search(value):
            fail(f"job envelope field {key} contains control characters")
        normalized[key] = value
    return normalized


def resolve_action(action: str, pillar: str) -> dict | None:
    if not action:
        return None
    if not ACTION.fullmatch(action):
        fail("action name is invalid")
    matches = [item for item in catalog_actions() if item.get("action") == action and item.get("pillar") == pillar]
    if len(matches) != 1:
        fail("action is not registered to the requested pillar")
    return matches[0]


def validate_ref(value: str) -> None:
    if not base.REF.fullmatch(value):
        fail("invalid source_ref")
    if any(token in value for token in ("..", "//", "@{", "\\")):
        fail("invalid source_ref")
    if value.startswith("/") or value.endswith("/") or value.endswith(".") or value.endswith(".lock"):
        fail("invalid source_ref")


def execution_provenance() -> dict[str, str]:
    return {
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "trigger_actor": os.environ.get("GITHUB_ACTOR", ""),
        "trigger_actor_id": os.environ.get("GITHUB_ACTOR_ID", ""),
        "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
        "execution_repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "public_runner_sha": os.environ.get("GITHUB_SHA", ""),
    }


def build_plan(event_path: str, manual: dict[str, str]) -> dict:
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    event_name = str(event.get("action", ""))

    if event_name in EVENT_PILLARS:
        payload = validate_shape(event.get("client_payload") or {})
        event_pillar = EVENT_PILLARS[event_name]
        if event_name in EVENT_DEFAULT_ACTION:
            payload.setdefault("action", EVENT_DEFAULT_ACTION[event_name])
    else:
        payload = validate_shape(manual)
        event_pillar = ""

    declared_pillar = payload.get("pillar", "").upper()
    if event_pillar and declared_pillar and declared_pillar != event_pillar:
        fail("payload pillar conflicts with the ingress event")
    pillar = event_pillar or declared_pillar
    if pillar not in base.ALLOWED_TASKS:
        fail("unknown or missing pillar")

    job_id = payload.get("job_id", "")
    if not base.JOB_ID.fullmatch(job_id):
        fail("job_id must be 8-64 safe characters")

    action = payload.get("action", "")
    entry = resolve_action(action, pillar)
    source_ref = payload.get("source_ref", "main")
    validate_ref(source_ref)

    if entry:
        if "source_repo" in payload or "task" in payload:
            fail("catalog actions may not override source_repo or task")
        source_repo = str(entry["target_repo"])
        task = ADAPTER_TASK.get(str(entry["adapter"]))
        if not task:
            fail("catalog adapter is not registered")
    else:
        source_repo = payload.get("source_repo", "")
        task = payload.get("task", "")
        if not source_repo or not task:
            fail("base tasks require source_repo and task")
        if source_repo not in approved_workloads():
            fail("source_repo is not in the catalog-derived workload allowlist")
        if task not in base.ALLOWED_TASKS[pillar]:
            fail("task is not allowed for this pillar")

    if not base.REPO.fullmatch(source_repo):
        fail("source_repo must be an approved GlacierEQ repository")

    approval_required = pillar in {"G", "I"} or bool(entry and entry.get("approval_required"))
    approval_id = payload.get("approval_id", "")
    if approval_required and not base.JOB_ID.fullmatch(approval_id):
        fail("this action requires a valid private approval_id")
    if not approval_required and approval_id:
        fail("approval_id is accepted only for approval-gated actions")

    plan = {
        "job_id": job_id,
        "pillar": pillar,
        "source_repo": source_repo,
        "source_ref": source_ref,
        "task": task,
        "approval_id": approval_id,
        "approval_required": "true" if approval_required else "false",
        "action": str(entry["action"]) if entry else "",
        "adapter": str(entry["adapter"]) if entry else "",
        "target_repo": str(entry["target_repo"]) if entry else "",
    }
    plan.update(execution_provenance())
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
    base.emit_outputs(build_plan( args.event, manual))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
