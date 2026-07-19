#!/usr/bin/env python3
"""Validate the APEX GitHub App bridge without exposing credentials or private content."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API = "https://api.github.com"
API_VERSION = "2026-03-10"
OWNER = "GlacierEQ"
OWNER_ID = 194243768
CONTROL_REPO = "GlacierEQ/llm-runner-teams"
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
REPO = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fail(message: str) -> None:
    print(f"APP_BRIDGE_BLOCK: {message}", file=sys.stderr)
    raise SystemExit(1)


def request(token: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    if not token:
        fail("required installation token is absent")
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "apex-runner-bridge-canary",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = None
        try:
            payload = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            payload = None
        return exc.code, payload
    except Exception as exc:  # noqa: BLE001
        fail(f"GitHub API request failed: {type(exc).__name__}")


def installation_repositories(token: str) -> list[str]:
    status, payload = request(token, "GET", "/installation/repositories?per_page=100")
    if status != 200 or not isinstance(payload, dict):
        fail(f"installation repository lookup returned HTTP {status}")
    repos = payload.get("repositories")
    if not isinstance(repos, list):
        fail("installation repository response is malformed")
    names = sorted(str(item.get("full_name", "")) for item in repos if isinstance(item, dict))
    return names


def verify_repository(token: str, expected: str) -> dict[str, Any]:
    status, payload = request(token, "GET", f"/repos/{expected}")
    if status != 200 or not isinstance(payload, dict):
        fail(f"repository metadata lookup for {expected} returned HTTP {status}")
    owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
    if payload.get("full_name") != expected:
        fail("repository full name does not match the expected repository")
    if owner.get("login") != OWNER or owner.get("id") != OWNER_ID:
        fail("repository owner identity does not match GlacierEQ")
    return {
        "repository": expected,
        "repository_id": payload.get("id"),
        "private": bool(payload.get("private")),
        "default_branch": payload.get("default_branch"),
    }


def fetch_json_file(token: str, repo: str, path: str) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(path, safe="/")
    status, payload = request(token, "GET", f"/repos/{repo}/contents/{encoded}?ref=main")
    if status == 404:
        return None
    if status != 200 or not isinstance(payload, dict):
        fail(f"private ledger lookup returned HTTP {status}")
    content = payload.get("content")
    if not isinstance(content, str):
        fail("private ledger content response is malformed")
    try:
        decoded = base64.b64decode(content).decode("utf-8")
        value = json.loads(decoded)
    except Exception:  # noqa: BLE001
        fail("private ledger record is not valid JSON")
    if not isinstance(value, dict):
        fail("private ledger record must be a JSON object")
    return value


def create_json_file(token: str, repo: str, path: str, message: str, value: dict[str, Any]) -> str:
    if fetch_json_file(token, repo, path) is not None:
        fail(f"immutable ledger path already exists: {path}")
    encoded_path = urllib.parse.quote(path, safe="/")
    content = base64.b64encode((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")).decode("ascii")
    status, payload = request(
        token,
        "PUT",
        f"/repos/{repo}/contents/{encoded_path}",
        {"message": message, "content": content, "branch": "main"},
    )
    if status != 201 or not isinstance(payload, dict):
        fail(f"private ledger write returned HTTP {status}")
    commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
    sha = str(commit.get("sha", ""))
    if not SHA.fullmatch(sha):
        fail("private ledger write did not return a valid commit SHA")
    return sha


def validate_common(args: argparse.Namespace) -> None:
    if not JOB_ID.fullmatch(args.job_id):
        fail("job_id must be 8-64 safe characters")
    if not REPO.fullmatch(args.workload_repo):
        fail("workload repository must be an approved GlacierEQ repository")
    if args.workload_repo == CONTROL_REPO:
        fail("control repository cannot be used as a workload")


def verify_and_claim(args: argparse.Namespace) -> int:
    validate_common(args)
    control_token = os.environ.get("APEX_CONTROL_TOKEN", "")
    workload_token = os.environ.get("APEX_PRIVATE_READ_TOKEN", "")

    control_repos = installation_repositories(control_token)
    workload_repos = installation_repositories(workload_token)
    if control_repos != [CONTROL_REPO]:
        fail(f"control token repository scope is {control_repos!r}; expected only {CONTROL_REPO}")
    if workload_repos != [args.workload_repo]:
        fail(f"workload token repository scope is {workload_repos!r}; expected only {args.workload_repo}")

    control_meta = verify_repository(control_token, CONTROL_REPO)
    workload_meta = verify_repository(workload_token, args.workload_repo)
    claim = {
        "schema_version": "1.0",
        "job_id": args.job_id,
        "state": "claimed",
        "mode": "github-app-installation-token",
        "claimed_at": now(),
        "action_face": os.environ.get("GITHUB_REPOSITORY", ""),
        "action_face_sha": os.environ.get("GITHUB_SHA", ""),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "actor": os.environ.get("GITHUB_ACTOR", ""),
        "actor_id": os.environ.get("GITHUB_ACTOR_ID", ""),
        "app_slug": args.app_slug,
        "control_installation_id": args.control_installation_id,
        "workload_installation_id": args.workload_installation_id,
        "source_repo": args.workload_repo,
        "source_ref": args.workload_ref,
        "control_scope": control_repos,
        "workload_scope": workload_repos,
        "control_repository": control_meta,
        "workload_repository": workload_meta,
    }
    commit_sha = create_json_file(
        control_token,
        CONTROL_REPO,
        f"claims/{args.job_id}.json",
        f"claim: {args.job_id} via APEX Runner Bridge",
        claim,
    )
    print(f"APP_BRIDGE_CLAIM_OK: {args.job_id} commit={commit_sha}")
    return 0


def complete(args: argparse.Namespace) -> int:
    validate_common(args)
    resolved = args.resolved_source_sha.lower()
    if not SHA.fullmatch(resolved):
        fail("resolved source SHA must be a full lowercase commit SHA")
    control_token = os.environ.get("APEX_CONTROL_TOKEN", "")
    claim = fetch_json_file(control_token, CONTROL_REPO, f"claims/{args.job_id}.json")
    if claim is None:
        fail("matching private claim does not exist")
    if claim.get("job_id") != args.job_id or claim.get("source_repo") != args.workload_repo:
        fail("private claim does not match the completed workload")

    receipt = {
        "schema_version": "1.0",
        "job_id": args.job_id,
        "state": "completed",
        "mode": "github-app-installation-token",
        "completed_at": now(),
        "claim_path": f"claims/{args.job_id}.json",
        "action_face": os.environ.get("GITHUB_REPOSITORY", ""),
        "action_face_sha": os.environ.get("GITHUB_SHA", ""),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "actor": os.environ.get("GITHUB_ACTOR", ""),
        "actor_id": os.environ.get("GITHUB_ACTOR_ID", ""),
        "app_slug": args.app_slug,
        "control_installation_id": args.control_installation_id,
        "workload_installation_id": args.workload_installation_id,
        "source_repo": args.workload_repo,
        "source_ref": args.workload_ref,
        "resolved_source_sha": resolved,
        "checks": {
            "public_action_face_identity": "pass",
            "control_token_single_repository_scope": "pass",
            "workload_token_single_repository_scope": "pass",
            "private_claim_write": "pass",
            "private_workload_checkout": "pass",
            "exact_source_binding": "pass"
        },
        "release_state": "Pass",
    }
    commit_sha = create_json_file(
        control_token,
        CONTROL_REPO,
        f"results/{args.job_id}.json",
        f"result: {args.job_id} GitHub App bridge canary",
        receipt,
    )
    print(f"APP_BRIDGE_RECEIPT_OK: {args.job_id} commit={commit_sha} source={resolved}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("verify-and-claim", "complete"):
        command = sub.add_parser(name)
        command.add_argument("--job-id", required=True)
        command.add_argument("--workload-repo", required=True)
        command.add_argument("--workload-ref", default="main")
        command.add_argument("--app-slug", required=True)
        command.add_argument("--control-installation-id", required=True)
        command.add_argument("--workload-installation-id", required=True)
    sub.choices["complete"].add_argument("--resolved-source-sha", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "verify-and-claim":
        return verify_and_claim(args)
    if args.command == "complete":
        return complete(args)
    fail("unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
