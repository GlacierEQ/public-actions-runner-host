#!/usr/bin/env python3
"""Fail closed unless the private control plane has the required non-executing, append-only state."""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CONTROL_REPO = os.environ.get("APEX_CONTROL_REPO", "GlacierEQ/llm-runner-teams")
EXPECTED_EXECUTION_PLANE = "GlacierEQ/public-actions-runner-host"


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


def read_json_file(path: str, token: str) -> dict:
    response = request(f"contents/{path}", token)
    if not isinstance(response, dict) or "content" not in response:
        fail(f"{path} could not be read")
    try:
        decoded = base64.b64decode(response["content"]).decode("utf-8")
        data = json.loads(decoded)
    except Exception as exc:  # noqa: BLE001
        fail(f"{path} is invalid: {type(exc).__name__}")
    if not isinstance(data, dict):
        fail(f"{path} must contain a JSON object")
    return data


def main() -> int:
    token = os.environ.get("APEX_CONTROL_TOKEN", "")
    if not token:
        fail("APEX_CONTROL_TOKEN is required")

    metadata = request("", token)
    if not isinstance(metadata, dict):
        fail("control-plane repository metadata was malformed")
    owner = metadata.get("owner") or {}
    checks = {
        "full_name": metadata.get("full_name") == CONTROL_REPO,
        "owner": owner.get("login") == "GlacierEQ",
        "visibility": metadata.get("visibility") == "private",
        "private": metadata.get("private") is True,
        "archived": metadata.get("archived") is False,
        "disabled": metadata.get("disabled") is False,
        "fork": metadata.get("fork") is False,
        "default_branch": metadata.get("default_branch") == "main",
    }
    failed_metadata = sorted(name for name, passed in checks.items() if not passed)
    if failed_metadata:
        fail(f"control-plane repository identity/state mismatch: {', '.join(failed_metadata)}")

    workflow_items = request("contents/.github/workflows", token)
    if not isinstance(workflow_items, list):
        fail("control-plane workflows directory response was malformed")
    executable = [
        str(item.get("name", ""))
        for item in workflow_items
        if str(item.get("name", "")).lower().endswith((".yml", ".yaml"))
    ]
    if executable:
        fail(f"private executable workflows detected: {', '.join(sorted(executable))}")

    actions_policy = read_json_file("policy/no-private-actions.json", token)
    if actions_policy.get("status") != "active":
        fail("no-private-actions policy is not active")
    if actions_policy.get("control_plane", {}).get("repository") != CONTROL_REPO:
        fail("no-private-actions policy points to the wrong control plane")
    if actions_policy.get("control_plane", {}).get("github_actions") != "forbidden":
        fail("control-plane policy does not forbid GitHub Actions")
    if actions_policy.get("execution_plane", {}).get("repository") != EXPECTED_EXECUTION_PLANE:
        fail("control-plane policy points to the wrong execution plane")
    if actions_policy.get("execution_plane", {}).get("required_visibility") != "public":
        fail("execution-plane visibility policy is not public")
    if actions_policy.get("private_workload_repositories", {}).get("github_actions") != "forbidden":
        fail("private workload policy does not forbid GitHub Actions")

    result_policy = read_json_file("policy/immutable-results.json", token)
    if result_policy.get("status") != "active":
        fail("immutable-results policy is not active")
    if result_policy.get("one_job_id_one_receipt") is not True:
        fail("immutable-results policy does not require one receipt per job ID")
    if result_policy.get("overwrite_allowed") is not False:
        fail("immutable-results policy permits overwrite")
    if result_policy.get("delete_allowed") is not False:
        fail("immutable-results policy permits deletion")
    if result_policy.get("execution_plane") != EXPECTED_EXECUTION_PLANE:
        fail("immutable-results policy points to the wrong execution plane")
    if result_policy.get("path_pattern") != "results/<job_id>.json":
        fail("immutable-results policy uses an unexpected result path")
    required_receipt = set(result_policy.get("required_receipt_fields") or [])
    expected_receipt = {
        "published_at",
        "payload_sha256",
        "workflow_run_id",
        "workflow_run_attempt",
        "public_runner_sha",
        "execution_repo",
    }
    if not expected_receipt.issubset(required_receipt):
        fail("immutable-results policy is missing required receipt provenance fields")

    print(f"CONTROL_PLANE_OK: {CONTROL_REPO} is private, non-executing, and append-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
