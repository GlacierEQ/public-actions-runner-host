#!/usr/bin/env python3
"""Post a sanitized public issue status and optionally close the issue."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

SAFE = re.compile(r"^[A-Za-z0-9_.:/\- ]{0,180}$")


def clean(name: str, value: object) -> str:
    text = str(value or "")[:180]
    if not SAFE.fullmatch(text):
        return f"invalid-{name}"
    return text


def api(url: str, token: str, method: str, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "apex-public-status")
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"public issue status API failed with status {exc.code}") from exc


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number = clean("issue", os.environ.get("ISSUE_NUMBER", ""))
    mode = os.environ.get("STATUS_MODE", "result")
    if not token or not repository or not issue_number.isdigit():
        raise SystemExit("GITHUB_TOKEN, GITHUB_REPOSITORY, and numeric ISSUE_NUMBER are required")

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = clean("run", os.environ.get("GITHUB_RUN_ID", ""))
    api_root = f"https://api.github.com/repos/{repository}/issues/{issue_number}"

    close = False
    close_reason = "completed"
    if mode == "rejected":
        reason = clean("reason", os.environ.get("AUTH_REASON", "unauthorized ingress"))
        body = "\n".join([
            "APEX public action face rejected this request.",
            "",
            f"- Reason: {reason}",
            f"- Run: {server}/{repository}/actions/runs/{run_id}",
            "",
            "No workload was checked out or executed.",
        ])
        close = True
        close_reason = "not_planned"
    else:
        job_id = clean("job", os.environ.get("JOB_ID", ""))
        pillar = clean("pillar", os.environ.get("PILLAR", ""))
        action = clean("action", os.environ.get("ACTION_NAME", "")) or "base-task"
        task = clean("task", os.environ.get("TASK", ""))
        runner_outcome = clean("runner", os.environ.get("RUNNER_OUTCOME", ""))
        publish_outcome = clean("publish", os.environ.get("PUBLISH_OUTCOME", ""))

        if runner_outcome == "success" and publish_outcome == "success":
            public_state = "completed"
            private_sink = "success recorded privately"
            close = True
        elif runner_outcome == "failure" and publish_outcome == "success":
            public_state = "execution failed"
            private_sink = "failure recorded privately"
        elif runner_outcome == "skipped":
            public_state = "execution did not start"
            private_sink = "no detailed execution result was created"
        else:
            public_state = "blocked"
            private_sink = "private result publication failed"

        body = "\n".join([
            "APEX public action face finished.",
            "",
            f"- Job: {job_id}",
            f"- Pillar: {pillar}",
            f"- Action: {action}",
            f"- Task: {task}",
            f"- State: {public_state}",
            f"- Detailed result: {private_sink}",
            f"- Run: {server}/{repository}/actions/runs/{run_id}",
            "",
            "Protected workload output was not published here.",
        ])

    api(f"{api_root}/comments", token, "POST", {"body": body})
    if close:
        api(api_root, token, "PATCH", {"state": "closed", "state_reason": close_reason})
    print("PUBLIC_STATUS_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"PUBLIC_STATUS_BLOCK: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
