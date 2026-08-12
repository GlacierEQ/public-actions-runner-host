#!/usr/bin/env python3
"""Content-scan every tracked file in active public original repositories.

Phase 0 proved complete repository/tree enumeration. Phase 1 now clones exact
public heads and walks every tracked file for active, non-fork, non-archived
repositories. The scanner never stores secret values or full source bodies; it
stores compact evidence about content, unfinished/fake-completion markers,
runtime/build/test/deploy surfaces, and complete file-scan coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".ex", ".exs", ".go", ".h", ".hpp", ".hs",
    ".java", ".js", ".jsx", ".kt", ".kts", ".mjs", ".php", ".proto", ".py",
    ".rb", ".rs", ".scala", ".sh", ".sol", ".sql", ".swift", ".ts", ".tsx",
}
TEXT_EXTENSIONS = SOURCE_EXTENSIONS | {
    ".css", ".csv", ".env", ".graphql", ".html", ".ini", ".json", ".jsonl",
    ".md", ".rst", ".toml", ".txt", ".xml", ".yaml", ".yml",
}

MARKERS: dict[str, re.Pattern[str]] = {
    "SCAFFOLD_STUB": re.compile(r"\bSCAFFOLD[ _-]+STUB\b", re.I),
    "SCAFFOLD_ONLY": re.compile(r"\bscaffold(?:\s+only|\s+state|\s+stub)\b", re.I),
    "PLACEHOLDER": re.compile(r"\bplaceholder\b", re.I),
    "TODO": re.compile(r"\bTODO\b"),
    "FIXME": re.compile(r"\bFIXME\b"),
    "NOT_IMPLEMENTED": re.compile(r"NotImplemented(?:Error)?|not[ _-]implemented", re.I),
    "FUTURE_AGENT": re.compile(r"(?:next|future|filling)\s+(?:ai|agent|engineer)|implementation\s+is\s+the\s+next", re.I),
    "MOCK_RUNTIME": re.compile(r"\bmock(?:ed)?\s+(?:db|database|api|runtime|integration|response|write|call|connection)\b", re.I),
    "FAKE_SUCCESS": re.compile(r"\bfake[ _-]?(?:success|integration|api|write|receipt)\b", re.I),
    "SKIP_ALL": re.compile(r"skip(?:Test|_all|all)|pytest\.mark\.skip", re.I),
}

SENSITIVE_NAME = re.compile(r"(^|[_.-])(secret|token|password|passwd|credential|private[-_]?key|api[-_]?key)([_.-]|$)", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|spec|specs|e2e)(/|\.)|(^|/).+\.(test|spec)\.[^.]+$", re.I)
WORKFLOW_PATH = re.compile(r"^\.github/workflows/.+\.(yml|yaml)$", re.I)
DEPLOY_PATH = re.compile(
    r"(^|/)(dockerfile|vercel\.json|fly\.toml|railway\.json|render\.yaml)$|"
    r"(^|/)(terraform|pulumi|helm|k8s|deploy|deployment|infra|infrastructure)(/|[-_.])|"
    r"^\.github/workflows/(deploy|release|publish)", re.I,
)
EXECUTION_BASENAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod",
    "makefile", "justfile", "dockerfile", "vercel.json", "composer.json", "gemfile",
    "pom.xml", "build.gradle", "build.gradle.kts",
}


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"command_failed:{cmd[0]}:{proc.returncode}:{proc.stderr[-500:]}")
    return proc.stdout


def likely_text(path: Path, sample: bytes) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS or path.name.lower() in EXECUTION_BASENAMES:
        return b"\x00" not in sample
    if b"\x00" in sample:
        return False
    if not sample:
        return True
    printable = sum(byte in b"\n\r\t\f\b" or 32 <= byte < 127 for byte in sample)
    return printable / len(sample) >= 0.85


def safe_marker_lines(text: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts: dict[str, int] = {}
    hits: list[dict[str, Any]] = []
    for name, pattern in MARKERS.items():
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        counts[name] = len(matches)
        # Record line numbers only, never the source line itself. This preserves
        # evidence location without accidentally copying credentials or PII.
        line_numbers = sorted({text.count("\n", 0, match.start()) + 1 for match in matches})[:25]
        hits.append({"marker": name, "count": len(matches), "lines": line_numbers})
    return counts, hits


def parse_package_scripts(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in sorted(scripts.items()) if isinstance(k, str)}


def inspect_repo(record: dict[str, Any], destination: Path) -> dict[str, Any]:
    full_name = record["full_name"]
    clone_url = f"https://github.com/{full_name}.git"
    started_head = record.get("default_branch") or "main"
    with tempfile.TemporaryDirectory(prefix="crystal-repo-") as tmp:
        repo = Path(tmp) / "repo"
        try:
            run(["git", "clone", "--quiet", "--depth=1", "--filter=blob:none", "--no-tags", "--branch", started_head, clone_url, str(repo)], timeout=300)
            head_sha = run(["git", "rev-parse", "HEAD"], cwd=repo).strip()
            index = run(["git", "ls-files", "-s", "-z"], cwd=repo)
        except Exception as exc:
            return {
                "repository": full_name,
                "repository_id": record["id"],
                "status": "ERROR",
                "completion_claim_allowed": False,
                "error": f"{type(exc).__name__}:{exc}",
            }

        tracked: list[tuple[str, str, str]] = []
        for raw in index.split("\x00"):
            if not raw:
                continue
            meta, path = raw.split("\t", 1)
            mode, blob_sha, _stage = meta.split(" ", 2)
            tracked.append((path, mode, blob_sha))

        totals = Counter()
        marker_totals = Counter()
        marker_files: list[dict[str, Any]] = []
        sensitive_named_paths: list[str] = []
        test_files: list[str] = []
        workflow_files: list[str] = []
        deployment_files: list[str] = []
        execution_surfaces: list[str] = []
        package_scripts: dict[str, str] = {}
        unreadable: list[dict[str, str]] = []
        extension_counts = Counter()

        for rel, mode, blob_sha in tracked:
            totals["tracked_entries"] += 1
            if mode == "160000":
                totals["submodules"] += 1
                continue
            file = repo / rel
            if mode == "120000":
                totals["symlinks"] += 1
            if not file.exists() and not file.is_symlink():
                unreadable.append({"path": rel, "reason": "tracked_path_missing_after_clone"})
                continue
            try:
                raw = file.read_bytes()
            except Exception as exc:
                unreadable.append({"path": rel, "reason": f"{type(exc).__name__}"})
                continue
            totals["files_read"] += 1
            totals["bytes_read"] += len(raw)
            suffix = file.suffix.lower() or "<none>"
            extension_counts[suffix] += 1
            if file.suffix.lower() in SOURCE_EXTENSIONS:
                totals["source_files"] += 1
            if TEST_PATH.search(rel):
                totals["test_files"] += 1
                if len(test_files) < 100:
                    test_files.append(rel)
            if WORKFLOW_PATH.search(rel):
                totals["workflow_files"] += 1
                if len(workflow_files) < 100:
                    workflow_files.append(rel)
            if DEPLOY_PATH.search(rel):
                totals["deployment_files"] += 1
                if len(deployment_files) < 100:
                    deployment_files.append(rel)
            base = file.name.lower()
            lower = rel.lower()
            if base in EXECUTION_BASENAMES or re.search(r"(^|/)(main|index|server|app|cli)\.(py|js|mjs|ts|rs|go)$", lower):
                totals["execution_surfaces"] += 1
                if len(execution_surfaces) < 100:
                    execution_surfaces.append(rel)
            if SENSITIVE_NAME.search(rel):
                totals["sensitive_named_paths"] += 1
                if len(sensitive_named_paths) < 100:
                    sensitive_named_paths.append(rel)
            sample = raw[:8192]
            if likely_text(file, sample):
                totals["text_files"] += 1
                text = raw.decode("utf-8", errors="replace")
                totals["text_lines"] += text.count("\n") + (1 if text else 0)
                counts, hits = safe_marker_lines(text)
                marker_totals.update(counts)
                if hits and len(marker_files) < 500:
                    marker_files.append({"path": rel, "blob_sha": blob_sha, "markers": hits})
            else:
                totals["binary_files"] += 1
            if rel == "package.json":
                package_scripts = parse_package_scripts(file)

        coverage_complete = (
            totals["files_read"] + totals["submodules"] == totals["tracked_entries"]
            and not unreadable
        )
        evidence = {
            "schema": "glaciereq.crystallization.public-content-scan.v1",
            "repository": full_name,
            "repository_id": record["id"],
            "head_sha": head_sha,
            "default_branch": started_head,
            "tree_digest_phase0": record.get("tree_digest"),
            "status": "CONTENT_SCANNED" if coverage_complete else "CONTENT_SCAN_INCOMPLETE",
            "coverage_complete": coverage_complete,
            "completion_claim_allowed": False,
            "counts": dict(sorted(totals.items())),
            "marker_totals": dict(sorted(marker_totals.items())),
            "marker_files": marker_files,
            "sensitive_named_paths": sensitive_named_paths,
            "test_files": sorted(test_files),
            "workflow_files": sorted(workflow_files),
            "deployment_files": sorted(deployment_files),
            "execution_surfaces": sorted(execution_surfaces),
            "package_scripts": package_scripts,
            "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: (-item[1], item[0]))[:50]),
            "unreadable": unreadable,
        }
        evidence["evidence_digest"] = digest_json(evidence)
        safe_name = full_name.replace("/", "__") + ".json"
        (destination / safe_name).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return evidence


def scan_shard(registry_path: Path, output_dir: Path, shard_index: int, shard_count: int) -> int:
    registry = json.loads(registry_path.read_text())
    repos = [
        repo for repo in registry["repositories"]
        if not repo.get("fork") and not repo.get("archived") and not repo.get("disabled")
    ]
    repos.sort(key=lambda r: r["full_name"].lower())
    assigned = [repo for index, repo in enumerate(repos) if index % shard_count == shard_index]
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, repo in enumerate(assigned, start=1):
        print(f"[{shard_index}:{index}/{len(assigned)}] {repo['full_name']}", flush=True)
        results.append(inspect_repo(repo, output_dir))
    summary = {
        "schema": "glaciereq.crystallization.public-content-shard.v1",
        "shard_index": shard_index,
        "shard_count": shard_count,
        "assigned": len(assigned),
        "content_scanned": sum(r.get("status") == "CONTENT_SCANNED" for r in results),
        "incomplete": sum(r.get("status") != "CONTENT_SCANNED" for r in results),
        "repositories": [r["repository"] for r in results],
        "digests": {r["repository"]: r.get("evidence_digest") for r in results if r.get("evidence_digest")},
    }
    summary["summary_digest"] = digest_json(summary)
    (output_dir / f"_shard_{shard_index}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0 if summary["incomplete"] == 0 else 2


def merge(shards_dir: Path, output_dir: Path, expected_repos: int) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_files = sorted(shards_dir.rglob("GlacierEQ__*.json"))
    seen = set()
    summaries = []
    total_markers = Counter()
    incomplete = []
    for source in repo_files:
        data = json.loads(source.read_text())
        repo = data["repository"]
        if repo in seen:
            raise RuntimeError(f"duplicate_repository_evidence:{repo}")
        seen.add(repo)
        total_markers.update(data.get("marker_totals", {}))
        if data.get("status") != "CONTENT_SCANNED":
            incomplete.append(repo)
        target = output_dir / source.name
        shutil.copyfile(source, target)
        summaries.append({
            "repository": repo,
            "head_sha": data.get("head_sha"),
            "status": data.get("status"),
            "coverage_complete": data.get("coverage_complete"),
            "tracked_entries": data.get("counts", {}).get("tracked_entries", 0),
            "files_read": data.get("counts", {}).get("files_read", 0),
            "source_files": data.get("counts", {}).get("source_files", 0),
            "test_files": data.get("counts", {}).get("test_files", 0),
            "deployment_files": data.get("counts", {}).get("deployment_files", 0),
            "marker_total": sum(data.get("marker_totals", {}).values()),
            "evidence_digest": data.get("evidence_digest"),
        })
    summaries.sort(key=lambda item: item["repository"].lower())
    if len(summaries) != expected_repos:
        incomplete.append(f"COUNT:{len(summaries)}!={expected_repos}")
    aggregate = {
        "schema": "glaciereq.crystallization.public-content-index.v1",
        "scope": "ACTIVE_NONFORK_NONARCHIVED_PUBLIC_ORIGINALS",
        "repository_count": len(summaries),
        "expected_repository_count": expected_repos,
        "content_scan_complete": not incomplete,
        "completion_claim_allowed": False,
        "incomplete": incomplete,
        "marker_totals": dict(sorted(total_markers.items())),
        "repositories": summaries,
    }
    aggregate["index_digest"] = digest_json(aggregate)
    (output_dir.parent / "public-content-index.json").write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")
    return 0 if not incomplete else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="crystallization/public-estate-registry.json")
    parser.add_argument("--output-dir", default="crystallization/content-index")
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int, default=8)
    parser.add_argument("--merge-dir")
    parser.add_argument("--expected-repos", type=int, default=160)
    args = parser.parse_args()
    if args.merge_dir:
        return merge(Path(args.merge_dir), Path(args.output_dir), args.expected_repos)
    if args.shard_index is None:
        raise SystemExit("--shard-index is required unless --merge-dir is used")
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("invalid shard index")
    return scan_shard(Path(args.registry), Path(args.output_dir), args.shard_index, args.shard_count)


if __name__ == "__main__":
    raise SystemExit(main())
