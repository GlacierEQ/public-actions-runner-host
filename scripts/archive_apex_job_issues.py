#!/usr/bin/env python3
"""Archive open [APEX JOB] issue records before retiring them from the issue tracker.

The issue tracker is a human work surface, not the durable machine-job ledger. This
migration preserves each machine record verbatim in a repository artifact first,
then closes only the captured [APEX JOB] issues with state_reason=not_planned.

Closing an issue here is a tracker disposition only. It does not assert that the
underlying APEX job succeeded, failed, or even executed.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

TITLE_PREFIX = "[APEX JOB] "
USER_AGENT = "apex-job-issue-archiver/1.0"


class GitHubAPIError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable {name} is missing")
    return value


API_ROOT = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
REPOSITORY = require_env("GITHUB_REPOSITORY")
TOKEN = require_env("GITHUB_TOKEN")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "manual").strip() or "manual"
BRANCH = os.environ.get("ARCHIVE_BRANCH", os.environ.get("GITHUB_REF_NAME", "main")).strip() or "main"
SOURCE_SHA = os.environ.get("GITHUB_SHA", "").strip()


def api_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    max_attempts: int = 7,
) -> Any:
    url = f"{API_ROOT}{path}"
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code in {403, 429, 502, 503, 504}
            if retryable and attempt < max_attempts:
                retry_after = exc.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(60.0, 2.0 ** (attempt - 1))
                except ValueError:
                    delay = min(60.0, 2.0 ** (attempt - 1))
                print(
                    f"GitHub API {exc.code} for {method} {path}; retry {attempt}/{max_attempts} in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise GitHubAPIError(
                f"GitHub API {exc.code} for {method} {path}: {response_body[:1000]}"
            ) from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                delay = min(30.0, 2.0 ** (attempt - 1))
                print(
                    f"network error for {method} {path}: {exc}; retry {attempt}/{max_attempts} in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise GitHubAPIError(f"network error for {method} {path}: {exc}") from exc

    raise AssertionError("unreachable")


def open_apex_job_issues() -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    page = 1
    owner_repo = urllib.parse.quote(REPOSITORY, safe="/")
    while True:
        items = api_request(
            "GET",
            f"/repos/{owner_repo}/issues?state=open&per_page=100&page={page}",
        )
        if not isinstance(items, list):
            raise GitHubAPIError("issues endpoint returned a non-list payload")
        for issue in items:
            if "pull_request" in issue:
                continue
            title = issue.get("title") or ""
            if not title.startswith(TITLE_PREFIX):
                continue
            captured.append(
                {
                    "issue_number": issue["number"],
                    "title": title,
                    "body": issue.get("body"),
                    "html_url": issue.get("html_url"),
                    "state": issue.get("state"),
                    "state_reason": issue.get("state_reason"),
                    "created_at": issue.get("created_at"),
                    "updated_at": issue.get("updated_at"),
                    "author": (issue.get("user") or {}).get("login"),
                    "author_association": issue.get("author_association"),
                }
            )
        if len(items) < 100:
            break
        page += 1
    captured.sort(key=lambda record: record["issue_number"])
    return captured


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def put_archive(
    path: str,
    manifest: dict[str, Any],
    *,
    message: str,
    existing_sha: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(manifest_bytes(manifest)).decode("ascii"),
        "branch": BRANCH,
    }
    if existing_sha:
        payload["sha"] = existing_sha
    encoded_path = urllib.parse.quote(path, safe="/")
    result = api_request("PUT", f"/repos/{REPOSITORY}/contents/{encoded_path}", payload)
    if not isinstance(result, dict) or not (result.get("content") or {}).get("sha"):
        raise GitHubAPIError("archive content write did not return a content SHA")
    return result


def close_issue(number: int) -> None:
    api_request(
        "PATCH",
        f"/repos/{REPOSITORY}/issues/{number}",
        {"state": "closed", "state_reason": "not_planned"},
    )


def set_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> int:
    captured = open_apex_job_issues()
    archive_path = f"jobs/archive/apex-job-issues-{RUN_ID}.json"
    captured_at = datetime.now(timezone.utc).isoformat()

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "purpose": "Archive machine-job issue records before removing them from the human issue backlog.",
        "repository": REPOSITORY,
        "source_branch": BRANCH,
        "source_workflow_sha": SOURCE_SHA or None,
        "captured_at": captured_at,
        "title_prefix": TITLE_PREFIX,
        "record_count": len(captured),
        "tracker_disposition_semantics": (
            "state_reason=not_planned means the machine record no longer belongs in the issue tracker; "
            "it does not assert job success, failure, or execution."
        ),
        "records": captured,
        "closure": {
            "status": "pending",
            "closed_count": 0,
            "closed_issue_numbers": [],
            "verified_remaining_open": None,
            "completed_at": None,
        },
    }

    initial = put_archive(
        archive_path,
        manifest,
        message=f"archive: capture {len(captured)} open APEX job issue records",
    )
    archive_sha = initial["content"]["sha"]
    initial_commit = (initial.get("commit") or {}).get("sha")
    print(f"archived {len(captured)} records to {archive_path} before closure")

    closed: list[int] = []
    for record in captured:
        number = int(record["issue_number"])
        close_issue(number)
        closed.append(number)
        # Pace writes to avoid GitHub's secondary abuse-rate throttle.
        time.sleep(1.05)

    remaining = open_apex_job_issues()
    if remaining:
        remaining_numbers = [record["issue_number"] for record in remaining]
        raise RuntimeError(
            f"post-closure verification failed: {len(remaining)} open APEX job issues remain: {remaining_numbers}"
        )

    manifest["closure"] = {
        "status": "verified",
        "closed_count": len(closed),
        "closed_issue_numbers": closed,
        "verified_remaining_open": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    final = put_archive(
        archive_path,
        manifest,
        message=f"archive: verify retirement of {len(closed)} APEX job issues",
        existing_sha=archive_sha,
    )
    final_commit = (final.get("commit") or {}).get("sha")

    set_output("archive_path", archive_path)
    set_output("captured_count", str(len(captured)))
    set_output("closed_count", str(len(closed)))
    set_output("remaining_open", "0")
    if initial_commit:
        set_output("initial_archive_commit", initial_commit)
    if final_commit:
        set_output("verified_archive_commit", final_commit)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## APEX job issue archive migration\n\n")
            handle.write(f"- Captured before closure: **{len(captured)}**\n")
            handle.write(f"- Closed as tracker-only `not_planned`: **{len(closed)}**\n")
            handle.write("- Verified remaining open `[APEX JOB]` issues: **0**\n")
            handle.write(f"- Durable archive: `{archive_path}`\n")

    print(f"verified: {len(closed)} archived records retired; 0 open APEX job issues remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
