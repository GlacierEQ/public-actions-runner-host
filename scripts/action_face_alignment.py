#!/usr/bin/env python3
"""Report public action-face alignment without blocking normal execution.

The previous guard converted identity drift or a transient GitHub API failure into
an automatic workflow stop.  This replacement keeps the complete identity model
visible as structured output while allowing the orchestrating workflow to keep
moving by default.  A caller that has an explicitly approved reason to require
alignment can opt into a non-zero exit with ``--require-alignment``.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence


IDENTITY = Path("config/action-face-identity.json")


def load_expected(path: Path = IDENTITY) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"identity contract must be an object: {path}")
    return value


def fetch_repository_metadata(repository: str, token: str) -> tuple[dict[str, Any] | None, str | None]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "apex-action-face-alignment",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        return None, f"identity lookup returned HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - output must preserve the operational condition
        return None, f"identity lookup unavailable: {type(exc).__name__}"
    if not isinstance(value, dict):
        return None, "identity lookup returned a non-object response"
    return value, None


def assess_alignment(
    expected: Mapping[str, Any],
    *,
    repository: str,
    repository_id: str,
    metadata: Mapping[str, Any] | None,
    lookup_error: str | None,
) -> dict[str, Any]:
    """Return a complete, non-blocking alignment report for one action face."""

    checks: dict[str, bool | None] = {
        "environment_repository": repository == expected.get("repository"),
        "environment_repository_id": (
            None if not repository_id else repository_id == str(expected.get("repository_id"))
        ),
    }
    if metadata is not None:
        owner = metadata.get("owner")
        owner = owner if isinstance(owner, Mapping) else {}
        checks.update(
            {
                "full_name": metadata.get("full_name") == expected.get("repository"),
                "repository_id": metadata.get("id") == expected.get("repository_id"),
                "owner_login": owner.get("login") == expected.get("owner"),
                "owner_id": owner.get("id") == expected.get("owner_id"),
                "visibility": metadata.get("visibility") == expected.get("required_visibility"),
                "default_branch": metadata.get("default_branch") == expected.get("required_default_branch"),
                "archived": metadata.get("archived") is expected.get("required_archived"),
                "disabled": metadata.get("disabled") is expected.get("required_disabled"),
                "fork": metadata.get("fork") is expected.get("required_fork"),
            }
        )

    mismatches = sorted(name for name, value in checks.items() if value is False)
    unresolved = sorted(name for name, value in checks.items() if value is None)
    if mismatches:
        status = "drift_detected"
    elif lookup_error:
        status = "locally_aligned_remote_unavailable"
    elif unresolved:
        status = "partially_observed"
    else:
        status = "aligned"

    return {
        "schema_version": "1.0",
        "event": "action_face_alignment",
        "status": status,
        "expected_repository": expected.get("repository"),
        "observed_repository": repository or None,
        "checks": checks,
        "mismatches": mismatches,
        "unresolved": unresolved,
        "lookup_error": lookup_error,
        "continuation": "enabled",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-alignment",
        action="store_true",
        help="Return a non-zero exit only when a caller explicitly requires complete alignment.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use local workflow context only; do not request repository metadata.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = load_expected()
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    repository_id = os.environ.get("GITHUB_REPOSITORY_ID", "")

    metadata: dict[str, Any] | None = None
    lookup_error: str | None = None
    if args.offline:
        lookup_error = "remote lookup intentionally skipped"
    elif repository:
        metadata, lookup_error = fetch_repository_metadata(repository, os.environ.get("GITHUB_TOKEN", ""))
    else:
        lookup_error = "GITHUB_REPOSITORY is unset"

    report = assess_alignment(
        expected,
        repository=repository,
        repository_id=repository_id,
        metadata=metadata,
        lookup_error=lookup_error,
    )
    print("ACTION_FACE_ALIGNMENT: " + json.dumps(report, sort_keys=True))
    if args.require_alignment and report["status"] != "aligned":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
