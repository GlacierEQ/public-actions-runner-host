#!/usr/bin/env python3
"""Fail closed unless the private control plane has the required non-executing state."""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CONTROL_REPO = os.environ.get("APEX_CONTROL_REPO", "GlacierEQ/llm-runner-teams")


def fail(message: str) -> None:
    print(f"CONTROL_PLANE_BLOCK: {message}", file=sys.stderr)
    raise SystemExit(1)


def request(path: str, token: str) -> object:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    url = f"https://api.github.com/repos/{CONTROL_REPO}/{encoded}" if path else f"https://api.github.com/repos/{CONTROL_REPO}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "apex-control-plane-guard",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        fail(f"GitHub API request for {path or 'repository'} failed with status {exc.code}")
    except Exception as exc:  # noqa: BLE001
        fail(f"GitHub API request for {path or 'repository'} failed: {type(exc).__name__}")


def main() -> int:
    token = os.environ.get("APEX_CONTROL_TOKEN", "")
    if not token:
        fail("APEX_CONTROL_TOKEN is required")

    metadata = request("", token)
    if not isinstance(metadata, dict):
        fail("control-plane repository metadata was malformed")
    if metadata.get("visibility") != "private" or metadata.get("private") is not True:
        fail("control-plane repository must remain private")
    if metadata.get("archived") is True:
        fail("control-plane repository is archived")

    workflow_items = request("contents/.github/workflows", token)
    if not isinstance(workflow_items, list):
        fail("control-plane workflows directory response was malformed")
    executable = [
        item.get("name", "")
        for item in workflow_items
        if str(item.get("name", "")).lower().endswith((".yml", ".yaml"))
    ]
    if executable:
        fail(f"private executable workflows detected: {', '.join(sorted(executable))}")

    policy = request("contents/policy/no-private-actions.json", token)
    if not isinstance(policy, dict) or "content" not in policy:
        fail("no-private-actions policy could not be read")
    try:
        policy_data = json.loads(base64.b64decode(policy["content"]).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"no-private-actions policy is invalid: {type(exc).__name__}")
    if policy_data.get("control_plane", {}).get("github_actions") != "forbidden":
        fail("control-plane policy does not forbid GitHub Actions")
    if policy_data.get("execution_plane", {}).get("repository") != "GlacierEQ/public-actions-runner-host":
        fail("control-plane policy points to the wrong execution plane")

    print(f"CONTROL_PLANE_OK: {CONTROL_REPO} is private and non-executing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
