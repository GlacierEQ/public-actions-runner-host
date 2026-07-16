#!/usr/bin/env python3
"""Authorize the principal behind a public action-face event."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

CONFIG = Path("config/authorized-actors.json")
SAFE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,80}$")


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def clean(value: object) -> str:
    text = str(value or "")[:160]
    return text if SAFE.fullmatch(text) else "untrusted-principal"


def deny(reason: str) -> None:
    output("authorized", "false")
    output("reason", reason.replace("\n", " ")[:160])
    print(f"ACTION_FACE_AUTH_BLOCK: {reason}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    actor_records = config.get("authorized_actors") or []
    if not isinstance(actor_records, list):
        deny("authorized actor policy is malformed")
    actors = {
        str(record.get("login")): record
        for record in actor_records
        if isinstance(record, dict) and record.get("login")
    }
    associations = set(config.get("authorized_issue_associations") or [])
    expected_owner = str(config.get("repository_owner") or "")

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    owner = repository.split("/", 1)[0] if "/" in repository else ""
    if owner != expected_owner:
        deny("repository owner does not match the authorization policy")

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    actor = os.environ.get("GITHUB_ACTOR", "")
    actor_id = os.environ.get("GITHUB_ACTOR_ID", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}

    principal = actor
    principal_id = actor_id
    association = ""
    if event_name == "issues":
        issue = event.get("issue") or {}
        user = issue.get("user") or {}
        principal = str(user.get("login") or actor)
        principal_id = str(user.get("id") or actor_id)
        association = str(issue.get("author_association") or "")
        if association not in associations:
            deny(f"issue association {clean(association)} is not authorized")
    elif event_name == "repository_dispatch":
        sender = event.get("sender") or {}
        principal = str(sender.get("login") or actor)
        principal_id = str(sender.get("id") or actor_id)
    elif event_name not in {"workflow_dispatch", "push"}:
        deny(f"event {clean(event_name)} is not an authorized ingress type")

    record = actors.get(principal)
    if not record:
        deny(f"principal {clean(principal)} is not authorized")
    roles = set(record.get("roles") or [])
    if event_name not in roles:
        deny(f"principal {clean(principal)} is not authorized for {clean(event_name)}")

    expected_id = str(record.get("id") or "")
    if expected_id and not principal_id:
        deny(f"principal {clean(principal)} did not provide a numeric identity")
    if expected_id and principal_id != expected_id:
        deny(f"principal {clean(principal)} numeric identity does not match policy")

    output("authorized", "true")
    output("reason", "authorized")
    output("principal", clean(principal))
    output("principal_id", clean(principal_id))
    print(f"ACTION_FACE_AUTH_OK: {clean(principal)}#{clean(principal_id)} via {clean(event_name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
