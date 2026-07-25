#!/usr/bin/env python3
"""Inventory and extinguish resolved GitHub branches without losing unique work.

This trusted control adapter runs only in the public execution plane. It never
passes write credentials into a checked-out workload. Apply mode deletes only
non-default branches whose head contains no commits absent from the repository's
canonical default branch. Unique progress remains blocked for later
ABSORB/TRANSPLANT/QUARANTINE resolution.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
CONTROL_REPO = os.environ.get("APEX_CONTROL_REPO", "GlacierEQ/llm-runner-teams")
API_ROOT = "https://api.github.com"
USER_AGENT = "apex-master-strand-extinction/1.0"


class ExtinctionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BranchDecision:
    repository: str
    default_branch: str
    branch: str
    default_sha: str
    branch_sha: str
    ahead_by: int
    behind_by: int
    compare_status: str
    disposition: str
    delete_ready: bool
    deleted: bool = False
    delete_status: str | None = None
    blocker: str | None = None


class GitHubAPI:
    def __init__(self, token: str):
        if not token:
            raise ExtinctionError("GitHub token is required")
        self.token = token

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        allow_status: Iterable[int] = (),
    ) -> tuple[Any, dict[str, str], int]:
        url = path if path.startswith("https://") else f"{API_ROOT}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", USER_AGENT)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                data = json.loads(raw.decode("utf-8")) if raw else None
                return data, dict(response.headers.items()), response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code in set(allow_status):
                data = json.loads(raw.decode("utf-8")) if raw else None
                return data, dict(exc.headers.items()), exc.code
            message = ""
            try:
                message = str(json.loads(raw.decode("utf-8")).get("message", ""))
            except Exception:  # noqa: BLE001
                message = raw.decode("utf-8", errors="replace")[:500]
            raise ExtinctionError(
                f"GitHub API {method} {path} failed with {exc.code}: {message}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExtinctionError(
                f"GitHub API {method} {path} failed: {type(exc).__name__}: {exc}"
            ) from exc

    def pages(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            data, _, _ = self.request(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(data, list):
                raise ExtinctionError(f"Expected list response from {path}")
            items.extend(item for item in data if isinstance(item, dict))
            if len(data) < 100:
                break
            page += 1
            if page > 100:
                raise ExtinctionError(f"Pagination limit exceeded for {path}")
        return items

    def owned_repositories(self, owner: str) -> list[dict[str, Any]]:
        repositories = self.pages(
            "/user/repos?affiliation=owner&visibility=all&sort=full_name&direction=asc"
        )
        return [
            repo
            for repo in repositories
            if str(repo.get("owner", {}).get("login", "")) == owner
        ]

    def branches(self, full_name: str) -> list[dict[str, Any]]:
        return self.pages(f"/repos/{full_name}/branches")

    def compare(self, full_name: str, base: str, head: str) -> dict[str, Any]:
        base_ref = urllib.parse.quote(base, safe="")
        head_ref = urllib.parse.quote(head, safe="")
        data, _, _ = self.request(
            f"/repos/{full_name}/compare/{base_ref}...{head_ref}"
        )
        if not isinstance(data, dict):
            raise ExtinctionError(f"Compare response is malformed for {full_name}:{head}")
        return data

    def delete_branch(self, full_name: str, branch: str) -> int:
        encoded = urllib.parse.quote(branch, safe="/")
        _, _, status = self.request(
            f"/repos/{full_name}/git/refs/heads/{encoded}",
            method="DELETE",
            allow_status=(404, 422),
        )
        return status


class ControlPlane:
    def __init__(self, token: str):
        if not token:
            raise ExtinctionError("APEX_CONTROL_TOKEN is required")
        self.api = GitHubAPI(token)

    def approval(self, approval_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(f"approvals/{approval_id}.json", safe="/")
        data, _, _ = self.api.request(
            f"/repos/{CONTROL_REPO}/contents/{encoded}?ref=main"
        )
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise ExtinctionError("Approval record is malformed")
        try:
            decoded = base64.b64decode(data["content"]).decode("utf-8")
            payload = json.loads(decoded)
        except Exception as exc:  # noqa: BLE001
            raise ExtinctionError(
                f"Approval record is invalid: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, dict):
            raise ExtinctionError("Approval record must be a JSON object")
        return payload


def validate_identity(owner: str, job_id: str, approval_id: str | None) -> None:
    if not OWNER.fullmatch(owner):
        raise ExtinctionError("owner is invalid")
    if not JOB_ID.fullmatch(job_id):
        raise ExtinctionError("job_id must be 8-64 safe characters")
    if approval_id and not JOB_ID.fullmatch(approval_id):
        raise ExtinctionError("approval_id must be 8-64 safe characters")


def validate_approval(
    approval: dict[str, Any],
    *,
    job_id: str,
    approval_id: str,
    owner: str,
    mode: str,
) -> None:
    expected = {
        "approval_id": approval_id,
        "job_id": job_id,
        "pillar": "F",
        "action": "master-strand-extinction",
        "owner": owner,
        "mode": mode,
    }
    if approval.get("approved") is not True:
        raise ExtinctionError("Approval is not active")
    for field, value in expected.items():
        if approval.get(field) != value:
            raise ExtinctionError(f"Approval field {field} does not match this job")
    if approval.get("delete_resolved_only") is not True:
        raise ExtinctionError("Approval must require delete_resolved_only")
    if approval.get("preserve_unique_progress") is not True:
        raise ExtinctionError("Approval must require preserve_unique_progress")


def branch_decision(
    api: GitHubAPI,
    repository: dict[str, Any],
    branch: dict[str, Any],
) -> BranchDecision:
    full_name = str(repository.get("full_name", ""))
    default_branch = str(repository.get("default_branch", ""))
    name = str(branch.get("name", ""))
    branch_sha = str(branch.get("commit", {}).get("sha", ""))
    if not full_name.startswith("GlacierEQ/"):
        raise ExtinctionError("Repository escaped the GlacierEQ ownership boundary")
    if not REPO.fullmatch(full_name.split("/", 1)[1]):
        raise ExtinctionError(f"Repository name is invalid: {full_name}")
    if not BRANCH.fullmatch(default_branch) or not BRANCH.fullmatch(name):
        raise ExtinctionError(f"Branch name is invalid in {full_name}")

    comparison = api.compare(full_name, default_branch, name)
    ahead_by = int(comparison.get("ahead_by", 0))
    behind_by = int(comparison.get("behind_by", 0))
    status = str(comparison.get("status", "unknown"))
    default_sha = str(comparison.get("base_commit", {}).get("sha", ""))
    head_sha = str(comparison.get("head_commit", {}).get("sha", branch_sha))

    if name == default_branch:
        return BranchDecision(
            repository=full_name,
            default_branch=default_branch,
            branch=name,
            default_sha=default_sha,
            branch_sha=head_sha,
            ahead_by=ahead_by,
            behind_by=behind_by,
            compare_status=status,
            disposition="ALIVE",
            delete_ready=False,
            blocker="default branch is the canonical working face",
        )

    if ahead_by == 0:
        return BranchDecision(
            repository=full_name,
            default_branch=default_branch,
            branch=name,
            default_sha=default_sha,
            branch_sha=head_sha,
            ahead_by=ahead_by,
            behind_by=behind_by,
            compare_status=status,
            disposition="DISCARD",
            delete_ready=True,
        )

    return BranchDecision(
        repository=full_name,
        default_branch=default_branch,
        branch=name,
        default_sha=default_sha,
        branch_sha=head_sha,
        ahead_by=ahead_by,
        behind_by=behind_by,
        compare_status=status,
        disposition="ABSORB_OR_TRANSPLANT",
        delete_ready=False,
        blocker=(
            f"branch contains {ahead_by} commit(s) not present on {default_branch}; "
            "integrate or transplant the functional delta before deletion"
        ),
    )


def run(owner: str, mode: str, job_id: str, approval_id: str | None) -> dict[str, Any]:
    validate_identity(owner, job_id, approval_id)
    if mode not in {"inventory", "apply"}:
        raise ExtinctionError("mode must be inventory or apply")

    read_token = os.environ.get("APEX_PRIVATE_READ_TOKEN", "")
    write_token = os.environ.get("APEX_BRANCH_WRITE_TOKEN", "")
    token = write_token if mode == "apply" else (read_token or write_token)
    if not token:
        required = "APEX_BRANCH_WRITE_TOKEN" if mode == "apply" else "APEX_PRIVATE_READ_TOKEN"
        raise ExtinctionError(f"{required} is required")

    approval_sha256 = None
    if mode == "apply":
        if not approval_id:
            raise ExtinctionError("apply mode requires approval_id")
        approval = ControlPlane(os.environ.get("APEX_CONTROL_TOKEN", "")).approval(
            approval_id
        )
        validate_approval(
            approval,
            job_id=job_id,
            approval_id=approval_id,
            owner=owner,
            mode=mode,
        )
        approval_sha256 = hashlib.sha256(
            json.dumps(approval, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    api = GitHubAPI(token)
    repositories = api.owned_repositories(owner)
    decisions: list[BranchDecision] = []
    errors: list[dict[str, str]] = []
    defaults_not_main: list[dict[str, str]] = []

    for repository in repositories:
        full_name = str(repository.get("full_name", ""))
        if repository.get("archived") or repository.get("disabled"):
            continue
        default_branch = str(repository.get("default_branch", ""))
        if default_branch != "main":
            defaults_not_main.append(
                {"repository": full_name, "default_branch": default_branch}
            )
        try:
            branch_records = api.branches(full_name)
        except ExtinctionError as exc:
            errors.append({"repository": full_name, "error": str(exc)})
            continue

        for branch in branch_records:
            try:
                decision = branch_decision(api, repository, branch)
                if mode == "apply" and decision.delete_ready:
                    status = api.delete_branch(full_name, decision.branch)
                    deleted = status in {204, 404}
                    decision = BranchDecision(
                        **{
                            **asdict(decision),
                            "deleted": deleted,
                            "delete_status": str(status),
                            "blocker": None if deleted else f"delete returned status {status}",
                        }
                    )
                decisions.append(decision)
            except ExtinctionError as exc:
                errors.append(
                    {
                        "repository": full_name,
                        "branch": str(branch.get("name", "")),
                        "error": str(exc),
                    }
                )

    nondefault = [item for item in decisions if item.disposition != "ALIVE"]
    delete_ready = [item for item in nondefault if item.delete_ready]
    deleted = [item for item in nondefault if item.deleted]
    unique = [item for item in nondefault if not item.delete_ready]

    status = "completed"
    if errors:
        status = "partial"
    if mode == "apply" and delete_ready and len(deleted) != len(delete_ready):
        status = "partial"

    return {
        "schema_version": "1.0",
        "job_id": job_id,
        "action": "master-strand-extinction",
        "mode": mode,
        "owner": owner,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "approval_id": approval_id,
        "approval_sha256": approval_sha256,
        "provenance": {
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "execution_repo": os.environ.get("GITHUB_REPOSITORY", ""),
            "public_runner_sha": os.environ.get("GITHUB_SHA", ""),
            "trigger_actor": os.environ.get("GITHUB_ACTOR", ""),
            "trigger_actor_id": os.environ.get("GITHUB_ACTOR_ID", ""),
        },
        "summary": {
            "repositories_scanned": len(repositories),
            "branches_seen": len(decisions),
            "nondefault_branches": len(nondefault),
            "delete_ready": len(delete_ready),
            "deleted": len(deleted),
            "unique_progress_branches": len(unique),
            "repositories_default_not_main": len(defaults_not_main),
            "errors": len(errors),
        },
        "default_branch_normalization": defaults_not_main,
        "decisions": [asdict(item) for item in decisions],
        "errors": errors,
        "truth_boundary": (
            "Apply mode deletes only branches with zero commits absent from the current "
            "default branch. Unique progress is never deleted by this adapter."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="GlacierEQ")
    parser.add_argument("--mode", choices=("inventory", "apply"), required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--approval-id", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(
            owner=args.owner,
            mode=args.mode,
            job_id=args.job_id,
            approval_id=args.approval_id or None,
        )
        exit_code = 0 if result["status"] == "completed" else 2
    except ExtinctionError as exc:
        result = {
            "schema_version": "1.0",
            "job_id": args.job_id,
            "action": "master-strand-extinction",
            "mode": args.mode,
            "owner": args.owner,
            "status": "blocked",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "exact_blocker": str(exc),
            "provenance": {
                "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
                "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                "execution_repo": os.environ.get("GITHUB_REPOSITORY", ""),
                "public_runner_sha": os.environ.get("GITHUB_SHA", ""),
            },
        }
        exit_code = 1
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = result.get("summary", {})
    print(
        f"master-strand {args.mode}: status={result['status']} "
        f"repos={summary.get('repositories_scanned', 0)} "
        f"nondefault={summary.get('nondefault_branches', 0)} "
        f"deleted={summary.get('deleted', 0)}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
