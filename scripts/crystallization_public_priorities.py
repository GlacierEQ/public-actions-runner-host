#!/usr/bin/env python3
"""Derive deterministic Phase-1 work queues from the complete public registry."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compact(repo: dict[str, Any]) -> dict[str, Any]:
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
        "gate_files": analysis.get("gate_files", 0),
        "deployment_files": analysis.get("deployment_files", 0),
        "execution_surfaces": analysis.get("execution_surfaces", []),
        "deployment_surfaces": analysis.get("deployment_surfaces", []),
        "gate_surface_exceeds_source_surface": bool(signals.get("gate_surface_exceeds_source_surface")),
        "tree_digest": repo.get("tree_digest"),
        "pushed_at": repo.get("pushed_at"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="crystallization/public-estate-registry.json")
    parser.add_argument("--output", default="crystallization/public-estate-priorities.json")
    args = parser.parse_args()

    registry = json.loads(Path(args.registry).read_text())
    if registry.get("phase0_complete") is not True or registry.get("public_only") is not True:
        raise SystemExit("refusing priority derivation from incomplete/non-public registry")
    repos = registry["repositories"]

    active_nonfork = [r for r in repos if not r.get("fork") and not r.get("archived") and not r.get("disabled")]
    gate_dominant = [
        compact(r) for r in active_nonfork
        if r.get("path_analysis", {}).get("signals", {}).get("gate_surface_exceeds_source_surface")
    ]
    deployable_untested = [
        compact(r) for r in active_nonfork
        if r.get("path_analysis", {}).get("signals", {}).get("has_deployment_surface")
        and not r.get("path_analysis", {}).get("signals", {}).get("has_tests")
    ]
    source_untested = [
        compact(r) for r in active_nonfork
        if r.get("path_analysis", {}).get("signals", {}).get("has_source")
        and not r.get("path_analysis", {}).get("signals", {}).get("has_tests")
        and not r.get("path_analysis", {}).get("signals", {}).get("has_deployment_surface")
    ]
    source_missing = [
        compact(r) for r in active_nonfork
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
    result = {
        "schema": "glaciereq.crystallization.public-priorities.v1",
        "source_registry_digest": registry["registry_digest"],
        "source_repository_count": registry["repository_count"],
        "scope": "PUBLIC_OWNER_REPOSITORIES",
        "completion_claim_allowed": False,
        "active_nonfork_nonarchived": len(active_nonfork),
        "queue_counts": counts,
        "overlap": overlap,
        "queues": queues,
        "rules": {
            "gate_dominant": "active nonfork repo whose gate/policy/contract/authority path count exceeds source path count",
            "deployable_untested": "active nonfork repo with deployment surface and no detected test surface",
            "source_untested": "active nonfork repo with source but no tests and no detected deployment surface",
            "source_missing": "active nonfork repo with no detected source path; requires intention/lineage reconstruction",
            "archive_lineage": "archived repository; must prove intentional retirement or verified successor",
            "fork_lineage": "fork; must establish intentional specialization, upstream mirror, or canonical successor",
        },
    }
    result["queue_digest"] = digest({"source_registry_digest": result["source_registry_digest"], "queues": queues})
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": "PASS",
        "source_repository_count": result["source_repository_count"],
        "active_nonfork_nonarchived": result["active_nonfork_nonarchived"],
        "queue_counts": counts,
        "queue_digest": result["queue_digest"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
