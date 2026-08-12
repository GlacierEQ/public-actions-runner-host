#!/usr/bin/env python3
"""Derive deterministic Phase-1 work queues from the complete public registry.

Phase-0 path names are useful but insufficient evidence of behavior. A repository
can expose a real native test contract through package metadata without naming a
file ``test_*`` or placing it under ``tests/``. Queue derivation therefore folds
in the full-content scan when available instead of manufacturing repair work from
a filename heuristic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXECUTABLE_TEST_SCRIPT_NAMES = ("test", "verify", "validate")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_content_evidence(content_index_dir: Path) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    if not content_index_dir.is_dir():
        return evidence
    for path in sorted(content_index_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        repository = record.get("repository")
        if isinstance(repository, str) and repository:
            evidence[repository] = record
    return evidence


def test_contracts(repo: dict[str, Any], content: dict[str, dict[str, Any]]) -> list[str]:
    """Return concrete test evidence, never CI/lint presence by itself."""
    contracts: list[str] = []
    analysis = repo.get("path_analysis", {})
    if analysis.get("signals", {}).get("has_tests"):
        contracts.append("path:test-file")

    record = content.get(repo["full_name"], {})
    if int(record.get("test_files", 0) or 0) > 0 and "content:test-file" not in contracts:
        contracts.append("content:test-file")

    scripts = record.get("package_scripts")
    if isinstance(scripts, dict):
        for name in EXECUTABLE_TEST_SCRIPT_NAMES:
            command = scripts.get(name)
            if isinstance(command, str) and command.strip():
                contracts.append(f"package:{name}")
    return sorted(set(contracts))


def compact(
    repo: dict[str, Any],
    *,
    contracts: list[str] | None = None,
) -> dict[str, Any]:
    analysis = repo.get("path_analysis", {})
    signals = analysis.get("signals", {})
    return {
        "repository": repo["full_name"],
        "repository_id": repo["id"],
        "description": repo.get("description"),
        "language": repo.get("language"),
        "size_kb": repo.get("size_kb", 0),
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "source_files": analysis.get("source_files", 0),
        "test_files": analysis.get("test_files", 0),
        "effective_test_contracts": contracts or [],
        "gate_files": analysis.get("gate_files", 0),
        "deployment_files": analysis.get("deployment_files", 0),
        "execution_surfaces": analysis.get("execution_surfaces", []),
        "deployment_surfaces": analysis.get("deployment_surfaces", []),
        "gate_surface_exceeds_source_surface": bool(signals.get("gate_surface_exceeds_source_surface")),
        "tree_digest": repo.get("tree_digest"),
        "pushed_at": repo.get("pushed_at"),
    }


def queue_document(name: str, items: list[dict[str, Any]], registry_digest: str, queue_digest: str) -> dict[str, Any]:
    return {
        "schema": "glaciereq.crystallization.public-queue.v2",
        "queue": name,
        "scope": "PUBLIC_OWNER_REPOSITORIES",
        "source_registry_digest": registry_digest,
        "parent_queue_digest": queue_digest,
        "count": len(items),
        "completion_claim_allowed": False,
        "repositories": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="crystallization/public-estate-registry.json")
    parser.add_argument("--output", default="crystallization/public-estate-priorities.json")
    parser.add_argument("--queue-dir", default="crystallization/queues")
    parser.add_argument("--content-index-dir", default="crystallization/content-index")
    args = parser.parse_args()

    registry = json.loads(Path(args.registry).read_text())
    if registry.get("phase0_complete") is not True or registry.get("public_only") is not True:
        raise SystemExit("refusing priority derivation from incomplete/non-public registry")
    repos = registry["repositories"]
    content = load_content_evidence(Path(args.content_index_dir))

    active_nonfork = [r for r in repos if not r.get("fork") and not r.get("archived") and not r.get("disabled")]
    contracts_by_repo = {r["full_name"]: test_contracts(r, content) for r in active_nonfork}

    gate_dominant = [
        compact(r, contracts=contracts_by_repo[r["full_name"]]) for r in active_nonfork
        if r.get("path_analysis", {}).get("signals", {}).get("gate_surface_exceeds_source_surface")
    ]
    deployable_untested = [
        compact(r, contracts=contracts_by_repo[r["full_name"]]) for r in active_nonfork
        if r.get("path_analysis", {}).get("signals", {}).get("has_deployment_surface")
        and not contracts_by_repo[r["full_name"]]
    ]
    source_untested = [
        compact(r, contracts=contracts_by_repo[r["full_name"]]) for r in active_nonfork
        if r.get("path_analysis", {}).get("signals", {}).get("has_source")
        and not contracts_by_repo[r["full_name"]]
        and not r.get("path_analysis", {}).get("signals", {}).get("has_deployment_surface")
    ]
    source_missing = [
        compact(r, contracts=contracts_by_repo[r["full_name"]]) for r in active_nonfork
        if not r.get("path_analysis", {}).get("signals", {}).get("has_source")
    ]
    archives = [compact(r) for r in repos if r.get("archived")]
    forks = [compact(r) for r in repos if r.get("fork") and not r.get("archived")]

    def order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda r: (-r["deployment_files"], -r["gate_files"], -r["source_files"], r["repository"].lower()))

    queues = {
        "GATE_DOMINANT": order(gate_dominant),
        "DEPLOYABLE_UNTESTED": order(deployable_untested),
        "SOURCE_UNTESTED": order(source_untested),
        "SOURCE_MISSING": order(source_missing),
        "ARCHIVE_LINEAGE": order(archives),
        "FORK_LINEAGE": order(forks),
    }
    membership = {name: {item["repository"] for item in items} for name, items in queues.items()}
    overlap = {
        "gate_and_deployable_untested": sorted(membership["GATE_DOMINANT"] & membership["DEPLOYABLE_UNTESTED"]),
        "gate_and_source_untested": sorted(membership["GATE_DOMINANT"] & membership["SOURCE_UNTESTED"]),
    }
    counts = {name: len(items) for name, items in queues.items()}
    repositories_with_content_evidence = sum(r["full_name"] in content for r in active_nonfork)
    repositories_with_effective_test_contracts = sum(bool(contracts_by_repo[r["full_name"]]) for r in active_nonfork)
    result = {
        "schema": "glaciereq.crystallization.public-priorities.v2",
        "source_registry_digest": registry["registry_digest"],
        "source_repository_count": registry["repository_count"],
        "scope": "PUBLIC_OWNER_REPOSITORIES",
        "completion_claim_allowed": False,
        "active_nonfork_nonarchived": len(active_nonfork),
        "content_evidence_records_used": repositories_with_content_evidence,
        "repositories_with_effective_test_contracts": repositories_with_effective_test_contracts,
        "queue_counts": counts,
        "overlap": overlap,
        "queues": queues,
        "rules": {
            "gate_dominant": "active nonfork repo whose gate/policy/contract/authority path count exceeds source path count",
            "deployable_untested": "active nonfork repo with deployment surface and no detected test file or executable package test/verify/validate contract",
            "source_untested": "active nonfork repo with source, no deployment surface, and no detected test file or executable package test/verify/validate contract",
            "source_missing": "active nonfork repo with no detected source path; requires intention/lineage reconstruction",
            "archive_lineage": "archived repository; must prove intentional retirement or verified successor",
            "fork_lineage": "fork; must establish intentional specialization, upstream mirror, or canonical successor",
        },
        "test_surface_semantics": {
            "recognized": ["phase0 test/spec/e2e path", "content-scan test file", "non-empty package test script", "non-empty package verify script", "non-empty package validate script"],
            "not_sufficient_alone": ["CI workflow presence", "lint script", "typecheck script", "documentation claim"],
        },
    }
    result["queue_digest"] = digest({"source_registry_digest": result["source_registry_digest"], "queues": queues})
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    queue_dir = Path(args.queue_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    for name, items in queues.items():
        queue_path = queue_dir / f"{name.lower().replace('_', '-')}.json"
        queue_path.write_text(
            json.dumps(queue_document(name, items, result["source_registry_digest"], result["queue_digest"]), indent=2, sort_keys=True) + "\n"
        )

    print(json.dumps({
        "status": "PASS",
        "source_repository_count": result["source_repository_count"],
        "active_nonfork_nonarchived": result["active_nonfork_nonarchived"],
        "content_evidence_records_used": repositories_with_content_evidence,
        "repositories_with_effective_test_contracts": repositories_with_effective_test_contracts,
        "queue_counts": counts,
        "queue_digest": result["queue_digest"],
        "queue_dir": str(queue_dir),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
