#!/usr/bin/env python3
"""Audit GlacierEQ branch lineage without deleting remote refs.

This adapter preserves the useful estate-wide inventory, comparison, provenance,
and receipt machinery of the former master-strand extinction path while retiring
its destructive authority. Commit containment is overlap evidence only. A donor
branch remains ACTIVE_IN_MESH unless whole-donor UNIQUE_CONTRIBUTION=0 is proven
outside this adapter; even then its terminal representation is preserved lineage,
not remote-ref deletion.
"""
from __future__ import annotations

import argparse
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
API_ROOT = "https://api.github.com"
USER_AGENT = "apex-master-strand-lineage-audit/2.0"


class ExtinctionError(RuntimeError):
    """Compatibility exception name retained for existing Action Face callers."""


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
    delete_ready: bool = False
    deleted: bool = False
    delete_status: str | None = None
    blocker: str | None = None
    unique_contribution_state: str = "CANDIDATE_UNVERIFIED"
    lineage_state: str = "ACTIVE_IN_MESH"


class GitHubAPI:
    """Read-only GitHub adapter for estate branch lineage observation."""

    def __init__(self, token: str):
        if not token:
            raise ExtinctionError("GitHub read token is required")
        self.token = token

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        allow_status: Iterable[int] = (),
    ) -> tuple[Any, dict[str, str], int]:
        if method != "GET":
            raise ExtinctionError(
                "Master-strand lineage audit is read-only; provider mutation is prohibited"
            )
        url = path if path.startswith("https://") else f"{API_ROOT}{path}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", USER_AGENT)
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
            try:
                message = str(json.loads(raw.decode("utf-8")).get("message", ""))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = raw.decode("utf-8", errors="replace")[:500]
            raise ExtinctionError(
                f"GitHub API GET {path} failed with {exc.code}: {message}"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ExtinctionError(
                f"GitHub API GET {path} failed: {type(exc).__name__}: {exc}"
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


def validate_identity(owner: str, job_id: str, approval_id: str | None) -> None:
    if not OWNER.fullmatch(owner):
        raise ExtinctionError("owner is invalid")
    if not JOB_ID.fullmatch(job_id):
        raise ExtinctionError("job_id must be 8-64 safe characters")
    if approval_id and not JOB_ID.fullmatch(approval_id):
        raise ExtinctionError("approval_id must be 8-64 safe characters")


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
            disposition="DEFAULT_BRANCH",
            blocker="default branch is an active lineage node",
            unique_contribution_state="NOT_APPLICABLE",
            lineage_state="ACTIVE_IN_MESH",
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
            disposition="PRESERVE_DRAINED_LINEAGE_CANDIDATE",
            blocker=(
                "Git commit containment establishes overlap only; whole-donor "
                "UNIQUE_CONTRIBUTION=0 has not been independently proven"
            ),
            unique_contribution_state="CANDIDATE_UNVERIFIED",
            lineage_state="ACTIVE_IN_MESH",
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
        blocker=(
            f"branch contains {ahead_by} commit(s) not present on {default_branch}; "
            "preserve and integrate its unique contributions"
        ),
        unique_contribution_state="NONZERO_COMMIT_DELTA_CONFIRMED",
        lineage_state="ACTIVE_IN_MESH",
    )


def run(owner: str, mode: str, job_id: str, approval_id: str | None) -> dict[str, Any]:
    """Run a read-only estate branch-lineage audit.

    ``apply`` remains accepted only as a compatibility input for existing Action Face
    callers. It is translated to read-only audit semantics and cannot acquire write
    credentials or delete refs.
    """
    validate_identity(owner, job_id, approval_id)
    if mode not in {"inventory", "apply"}:
        raise ExtinctionError("mode must be inventory or apply")

    requested_mode = mode
    effective_mode = "inventory"
    token = os.environ.get("APEX_PRIVATE_READ_TOKEN", "") or os.environ.get(
        "APEX_BRANCH_WRITE_TOKEN", ""
    )
    if not token:
        raise ExtinctionError("APEX_PRIVATE_READ_TOKEN or legacy read-capable token is required")

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
                decisions.append(branch_decision(api, repository, branch))
            except ExtinctionError as exc:
                errors.append(
                    {
                        "repository": full_name,
                        "branch": str(branch.get("name", "")),
                        "error": str(exc),
                    }
                )

    nondefault = [item for item in decisions if item.disposition != "DEFAULT_BRANCH"]
    overlap_only = [
        item
        for item in nondefault
        if item.disposition == "PRESERVE_DRAINED_LINEAGE_CANDIDATE"
    ]
    unique = [item for item in nondefault if item.ahead_by > 0]
    status = "partial" if errors else "completed"

    return {
        "schema_version": "2.0",
        "job_id": job_id,
        "action": "master-strand-lineage-audit",
        "legacy_action_alias": "master-strand-extinction",
        "requested_mode": requested_mode,
        "mode": effective_mode,
        "owner": owner,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "approval_id": approval_id,
        "provenance": {
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "execution_repo": os.environ.get("GITHUB_REPOSITORY", ""),
            "public_runner_sha": os.environ.get("GITHUB_SHA", ""),
            "trigger_actor": os.environ.get("GITHUB_ACTOR", ""),
            "trigger_actor_id": os.environ.get("GITHUB_ACTOR_ID", ""),
        },
        "anti_replacement": {
            "remote_ref_deletion_authority": False,
            "commit_containment_is_overlap_only": True,
            "whole_donor_zero_unique_contribution_required_for_retirement": True,
            "drained_terminal_state": "PRESERVE_DRAINED_LINEAGE",
        },
        "summary": {
            "repositories_scanned": len(repositories),
            "branches_seen": len(decisions),
            "nondefault_branches": len(nondefault),
            "overlap_only_candidates": len(overlap_only),
            "active_unique_progress_branches": len(unique),
            "delete_ready": 0,
            "deleted": 0,
            "repositories_default_not_main": len(defaults_not_main),
            "errors": len(errors),
        },
        "default_branch_normalization": defaults_not_main,
        "decisions": [asdict(item) for item in decisions],
        "errors": errors,
        "truth_boundary": (
            "Commit containment, merge state, age, or default-branch ancestry are overlap "
            "evidence only. This adapter cannot delete refs."
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
            "schema_version": "2.0",
            "job_id": args.job_id,
            "action": "master-strand-lineage-audit",
            "legacy_action_alias": "master-strand-extinction",
            "requested_mode": args.mode,
            "mode": "inventory",
            "owner": args.owner,
            "status": "blocked",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "exact_blocker": str(exc),
            "anti_replacement": {"remote_ref_deletion_authority": False},
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
        f"master-strand lineage audit: status={result['status']} "
        f"repos={summary.get('repositories_scanned', 0)} "
        f"nondefault={summary.get('nondefault_branches', 0)} "
        "deleted=0"
    )
    if args.mode == "apply":
        print(
            "legacy apply request translated to read-only lineage audit; ref deletion authority is retired",
            file=sys.stderr,
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
