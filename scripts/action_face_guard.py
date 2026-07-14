#!/usr/bin/env python3
"""Fail closed unless this workflow is executing from the canonical public action face."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

EXPECTED_REPO = os.environ.get("APEX_EXECUTION_REPO", "GlacierEQ/public-actions-runner-host")
EXPECTED_VISIBILITY = os.environ.get("APEX_EXPECTED_VISIBILITY", "public")


def fail(message: str) -> None:
    print(f"ACTION_FACE_BLOCK: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository != EXPECTED_REPO:
        fail(f"workflow repository is {repository or '<unset>'}; expected {EXPECTED_REPO}")

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
        fail(f"repository visibility lookup failed with status {exc.code}")
    except Exception as exc:  # noqa: BLE001
        fail(f"repository visibility lookup failed: {type(exc).__name__}")

    visibility = metadata.get("visibility")
    is_private = bool(metadata.get("private"))
    if is_private or visibility != EXPECTED_VISIBILITY:
        fail(f"execution repository visibility is {visibility!r}; expected {EXPECTED_VISIBILITY!r}")

    print(f"ACTION_FACE_OK: {repository} is the canonical {visibility} execution plane")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
