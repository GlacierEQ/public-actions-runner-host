#!/usr/bin/env python3
"""Fail closed unless this workflow is executing from the canonical public action face."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

IDENTITY = Path("config/action-face-identity.json")


def fail(message: str) -> None:
    print(f"ACTION_FACE_BLOCK: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    expected = json.loads(IDENTITY.read_text(encoding="utf-8"))
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository != expected.get("repository"):
        fail(f"workflow repository is {repository or '<unset>'}; expected {expected.get('repository')}")

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "apex-action-face-guard",
        },
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            metadata = json.load(response)
    except urllib.error.HTTPError as exc:
        fail(f"repository identity lookup failed with status {exc.code}")
    except Exception as exc:  # noqa: BLE001
        fail(f"repository identity lookup failed: {type(exc).__name__}")

    owner = metadata.get("owner") or {}
    checks = {
        "full_name": metadata.get("full_name") == expected.get("repository"),
        "repository_id": metadata.get("id") == expected.get("repository_id"),
        "owner_login": owner.get("login") == expected.get("owner"),
        "owner_id": owner.get("id") == expected.get("owner_id"),
        "visibility": metadata.get("visibility") == expected.get("required_visibility"),
        "private": metadata.get("private") is False,
        "default_branch": metadata.get("default_branch") == expected.get("required_default_branch"),
        "archived": metadata.get("archived") is expected.get("required_archived"),
        "disabled": metadata.get("disabled") is expected.get("required_disabled"),
        "fork": metadata.get("fork") is expected.get("required_fork"),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        fail(f"execution repository identity/state mismatch: {', '.join(failed)}")

    env_repo_id = os.environ.get("GITHUB_REPOSITORY_ID", "")
    if env_repo_id and env_repo_id != str(expected.get("repository_id")):
        fail("GITHUB_REPOSITORY_ID does not match the bound action-face identity")

    print(f"ACTION_FACE_OK: {repository} identity and public execution state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
