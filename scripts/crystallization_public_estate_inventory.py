#!/usr/bin/env python3
"""Build a complete, deterministic Phase-0 registry for GlacierEQ public repos.

This script is intentionally public-only. It enumerates every repository returned
by GitHub's public owner endpoint, verifies that count against profile.public_repos,
and walks every Git tree. If GitHub's recursive tree response truncates, it falls
back to explicit subtree traversal rather than silently accepting partial data.

It does NOT infer COMPLETE or CRYSTALLIZED from static structure. Its job is to
remove UNKNOWN from the public Phase-0 inventory while preserving every gap that
still requires semantic reconstruction and execution proof.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "GlacierEQ-Crystallization-Public-Inventory/1.0"

SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".ex", ".exs", ".go", ".h", ".hpp", ".hs",
    ".java", ".js", ".jsx", ".kt", ".kts", ".mjs", ".php", ".proto", ".py",
    ".rb", ".rs", ".scala", ".sh", ".sol", ".sql", ".swift", ".ts", ".tsx",
}
TEST_RE = re.compile(r"(^|/)(tests?|spec|specs|e2e)(/|\.)|(^|/).+\.(test|spec)\.[^.]+$", re.I)
GATE_RE = re.compile(r"(^|/)(gate|gates|policy|policies|contract|contracts|governance|authority|machine)(/|[-_.])", re.I)
DEPLOY_RE = re.compile(
    r"(^|/)(dockerfile|vercel\.json|fly\.toml|railway\.json|render\.yaml)$|"
    r"(^|/)(terraform|pulumi|helm|k8s|deploy|deployment|infra|infrastructure)(/|[-_.])|"
    r"^\.github/workflows/(deploy|release|publish)",
    re.I,
)
EXECUTION_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod",
    "makefile", "justfile", "dockerfile", "vercel.json", "composer.json", "gemfile",
    "pom.xml", "build.gradle", "build.gradle.kts",
}


class ApiError(RuntimeError):
    def __init__(self, status: int, path: str, body: str) -> None:
        super().__init__(f"GitHub API {status} for {path}: {body[:300]}")
        self.status = status
        self.path = path
        self.body = body


@dataclass
class GitHubApi:
    token: str | None

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        query = urlencode(params or {})
        url = f"{API}{path}" + (f"?{query}" if query else "")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                reset = exc.headers.get("x-ratelimit-reset")
                remaining = exc.headers.get("x-ratelimit-remaining")
                if remaining == "0" and reset and reset.isdigit():
                    delay = max(0, int(reset) - int(time.time())) + 1
                    if delay <= 120:
                        time.sleep(delay)
                        return self.get(path, params=params)
            raise ApiError(exc.code, path, body) from exc
        return json.loads(raw.decode("utf-8")) if raw else None


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def list_public_repositories(api: GitHubApi, owner: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile = api.get(f"/users/{quote(owner)}")
    expected = int(profile["public_repos"])
    repositories: list[dict[str, Any]] = []
    seen: set[int] = set()
    page = 1
    while True:
        batch = api.get(
            f"/users/{quote(owner)}/repos",
            params={"type": "owner", "sort": "full_name", "direction": "asc", "per_page": 100, "page": page},
        )
        if not isinstance(batch, list):
            raise RuntimeError("public repository listing did not return a list")
        for repo in batch:
            repo_id = int(repo["id"])
            if repo_id not in seen:
                seen.add(repo_id)
                repositories.append(repo)
        if len(batch) < 100:
            break
        page += 1
        if page > 100:
            raise RuntimeError("public repository pagination exceeded 10,000 repositories")
    if len(repositories) != expected:
        raise RuntimeError(f"public repository enumeration incomplete: listed={len(repositories)} profile.public_repos={expected}")
    repositories.sort(key=lambda repo: repo["full_name"].lower())
    return profile, repositories


def recursive_tree(api: GitHubApi, owner: str, repo: str, ref: str) -> tuple[list[dict[str, Any]], bool, int]:
    base = f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{quote(ref, safe='')}"
    try:
        first = api.get(base, params={"recursive": 1})
    except ApiError as exc:
        if exc.status in {409, 422}:
            return [], False, 1
        raise
    tree = first.get("tree", []) if isinstance(first, dict) else []
    if not first.get("truncated", False):
        return sorted(tree, key=lambda item: item.get("path", "")), False, 1

    # GitHub recursive tree responses can truncate around large repositories.
    # Fall back to explicit subtree traversal so "complete" really means complete.
    root = api.get(base)
    pending: list[tuple[str, str]] = []
    complete: list[dict[str, Any]] = []
    request_count = 2
    for entry in root.get("tree", []):
        item = dict(entry)
        complete.append(item)
        if entry.get("type") == "tree":
            pending.append((entry["path"], entry["sha"]))

    while pending:
        prefix, tree_sha = pending.pop()
        subtree = api.get(f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{tree_sha}")
        request_count += 1
        for entry in subtree.get("tree", []):
            full_path = f"{prefix}/{entry['path']}"
            item = dict(entry)
            item["path"] = full_path
            complete.append(item)
            if entry.get("type") == "tree":
                pending.append((full_path, entry["sha"]))
        if request_count > 20_000:
            raise RuntimeError(f"subtree walk exceeded 20,000 requests for {owner}/{repo}")
    complete.sort(key=lambda item: item.get("path", ""))
    return complete, True, request_count


def extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def classify_paths(paths: list[str]) -> dict[str, Any]:
    blobs = paths
    source = [p for p in blobs if extension(p) in SOURCE_EXTENSIONS]
    tests = [p for p in blobs if TEST_RE.search(p)]
    gates = [p for p in blobs if GATE_RE.search(p)]
    deployments = [p for p in blobs if DEPLOY_RE.search(p)]
    execution = []
    workflows = []
    for path in blobs:
        lower = path.lower()
        base = lower.rsplit("/", 1)[-1]
        if base in EXECUTION_NAMES or re.search(r"(^|/)(main|index|server|app|cli)\.(py|js|mjs|ts|rs|go)$", lower):
            execution.append(path)
        if lower.startswith(".github/workflows/") and lower.endswith((".yml", ".yaml")):
            workflows.append(path)
    return {
        "source_files": len(source),
        "test_files": len(tests),
        "gate_files": len(gates),
        "deployment_files": len(deployments),
        "workflow_files": len(workflows),
        "execution_surfaces": sorted(execution)[:100],
        "deployment_surfaces": sorted(deployments)[:100],
        "signals": {
            "has_source": bool(source),
            "has_tests": bool(tests),
            "has_gate_surface": bool(gates),
            "has_deployment_surface": bool(deployments),
            "gate_surface_exceeds_source_surface": bool(source) and len(gates) > len(source),
        },
    }


def inspect_repository(api: GitHubApi, repo: dict[str, Any]) -> dict[str, Any]:
    owner, name = repo["full_name"].split("/", 1)
    record: dict[str, Any] = {
        "id": repo["id"],
        "full_name": repo["full_name"],
        "description": repo.get("description"),
        "homepage": repo.get("homepage"),
        "language": repo.get("language"),
        "size_kb": repo.get("size", 0),
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "disabled": bool(repo.get("disabled")),
        "default_branch": repo.get("default_branch") or "main",
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "visibility": repo.get("visibility", "public"),
        "phase0_status": "DISCOVERED",
        "completion_claim_allowed": False,
    }
    try:
        entries, used_subtree_fallback, api_requests = recursive_tree(api, owner, name, record["default_branch"])
        blobs = [entry for entry in entries if entry.get("type") == "blob"]
        paths = [entry.get("path", "") for entry in blobs]
        record.update({
            "tree_status": "COMPLETE",
            "tree_entries": len(entries),
            "blob_files": len(blobs),
            "tree_digest": sha256_json([
                {"path": item.get("path"), "sha": item.get("sha"), "size": item.get("size"), "type": item.get("type")}
                for item in entries
            ]),
            "recursive_tree_truncation_recovered": used_subtree_fallback,
            "tree_api_requests": api_requests,
            "path_analysis": classify_paths(paths),
        })
    except Exception as exc:  # Preserve failure as evidence; never call the repo inspected.
        record.update({
            "tree_status": "ERROR",
            "phase0_status": "UNKNOWN",
            "tree_error": f"{type(exc).__name__}: {exc}",
            "completion_claim_allowed": False,
        })
    if record["archived"]:
        record["phase0_status"] = "ARCHIVED_REQUIRES_LINEAGE"
    return record


def build_registry(owner: str, workers: int) -> tuple[dict[str, Any], dict[str, Any]]:
    token = os.getenv("GITHUB_TOKEN") or None
    api = GitHubApi(token)
    profile, repositories = list_public_repositories(api, owner)
    records: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(inspect_repository, api, repo): repo["full_name"] for repo in repositories}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(f"[{index}/{len(repositories)}] {record['full_name']} {record['phase0_status']} tree={record.get('tree_status')}", file=sys.stderr)
    records.sort(key=lambda item: item["full_name"].lower())

    unknown = sum(record["phase0_status"] == "UNKNOWN" for record in records)
    archived = sum(record["phase0_status"] == "ARCHIVED_REQUIRES_LINEAGE" for record in records)
    discovered = sum(record["phase0_status"] == "DISCOVERED" for record in records)
    truncation_recovered = sum(bool(record.get("recursive_tree_truncation_recovered")) for record in records)
    registry_digest = sha256_json(records)
    registry = {
        "schema": "glaciereq.crystallization.public-estate-registry.v1",
        "owner": owner,
        "scope": "PUBLIC_OWNER_REPOSITORIES",
        "public_only": True,
        "profile_public_repos": int(profile["public_repos"]),
        "repository_count": len(records),
        "phase0_complete": unknown == 0 and len(records) == int(profile["public_repos"]),
        "completion_claim_allowed": False,
        "registry_digest": registry_digest,
        "repositories": records,
    }
    summary = {
        "schema": "glaciereq.crystallization.public-estate-summary.v1",
        "owner": owner,
        "scope": "PUBLIC_OWNER_REPOSITORIES",
        "public_only": True,
        "repository_count": len(records),
        "profile_public_repos": int(profile["public_repos"]),
        "phase0_complete": registry["phase0_complete"],
        "statuses": {
            "DISCOVERED": discovered,
            "ARCHIVED_REQUIRES_LINEAGE": archived,
            "UNKNOWN": unknown,
        },
        "tree_errors": sum(record.get("tree_status") == "ERROR" for record in records),
        "recursive_tree_truncations_recovered": truncation_recovered,
        "forks": sum(record["fork"] for record in records),
        "archived": sum(record["archived"] for record in records),
        "disabled": sum(record["disabled"] for record in records),
        "with_source": sum(bool(record.get("path_analysis", {}).get("signals", {}).get("has_source")) for record in records),
        "with_tests": sum(bool(record.get("path_analysis", {}).get("signals", {}).get("has_tests")) for record in records),
        "with_deployment_surface": sum(bool(record.get("path_analysis", {}).get("signals", {}).get("has_deployment_surface")) for record in records),
        "with_gate_surface": sum(bool(record.get("path_analysis", {}).get("signals", {}).get("has_gate_surface")) for record in records),
        "gate_surface_exceeds_source_surface": sum(bool(record.get("path_analysis", {}).get("signals", {}).get("gate_surface_exceeds_source_surface")) for record in records),
        "registry_digest": registry_digest,
        "completion_claim_allowed": False,
        "next_required_phase": "SEMANTIC_RECONSTRUCTION_AND_CAPABILITY_GAP_ANALYSIS",
    }
    return registry, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="GlacierEQ")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output-dir", default="crystallization")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 16:
        raise SystemExit("--workers must be between 1 and 16")
    registry, summary = build_registry(args.owner, args.workers)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "public-estate-registry.json").write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "public-estate-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["phase0_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
