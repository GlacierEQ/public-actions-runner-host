#!/usr/bin/env python3
"""Prioritize public-original metamorphosis from full tracked-file content scans."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".ex", ".exs", ".go", ".h", ".hpp", ".hs",
    ".java", ".js", ".jsx", ".kt", ".kts", ".mjs", ".php", ".proto", ".py",
    ".rb", ".rs", ".scala", ".sh", ".sol", ".sql", ".swift", ".ts", ".tsx",
}
SOURCE_MARKER_WEIGHT = {
    "SCAFFOLD_STUB": 160,
    "MOCK_RUNTIME": 140,
    "NOT_IMPLEMENTED": 120,
    "FAKE_SUCCESS": 120,
    "FUTURE_AGENT": 100,
    "SCAFFOLD_ONLY": 90,
    "PLACEHOLDER": 60,
    "FIXME": 30,
    "TODO": 15,
    "SKIP_ALL": 80,
}
NON_SOURCE_MARKER_WEIGHT = {
    "SCAFFOLD_STUB": 30,
    "MOCK_RUNTIME": 20,
    "NOT_IMPLEMENTED": 20,
    "FAKE_SUCCESS": 25,
    "FUTURE_AGENT": 15,
    "SCAFFOLD_ONLY": 15,
    "PLACEHOLDER": 5,
    "FIXME": 2,
    "TODO": 1,
    "SKIP_ALL": 10,
}


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_source(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(suffix) for suffix in SOURCE_SUFFIXES)


def score_manifest(data: dict[str, Any]) -> dict[str, Any]:
    score = 0
    reasons: list[dict[str, Any]] = []
    source_marker_counts: dict[str, int] = {}
    non_source_marker_counts: dict[str, int] = {}
    for file_hit in data.get("marker_files", []):
        path = file_hit["path"]
        source = is_source(path)
        weights = SOURCE_MARKER_WEIGHT if source else NON_SOURCE_MARKER_WEIGHT
        bucket = source_marker_counts if source else non_source_marker_counts
        for marker in file_hit.get("markers", []):
            name = marker["marker"]
            count = int(marker.get("count", 0))
            bucket[name] = bucket.get(name, 0) + count
            weight = weights.get(name, 0)
            contribution = min(count, 10) * weight
            score += contribution
            if weight:
                reasons.append({
                    "kind": "SOURCE_MARKER" if source else "NON_SOURCE_MARKER",
                    "path": path,
                    "marker": name,
                    "count": count,
                    "weight": weight,
                    "contribution": contribution,
                })

    counts = data.get("counts", {})
    source_files = int(counts.get("source_files", 0))
    test_files = int(counts.get("test_files", 0))
    deployment_files = int(counts.get("deployment_files", 0))
    execution_surfaces = int(counts.get("execution_surfaces", 0))
    if source_files and test_files == 0:
        score += 60
        reasons.append({"kind": "SOURCE_WITHOUT_TEST_SURFACE", "contribution": 60})
    if deployment_files and test_files == 0:
        score += 50
        reasons.append({"kind": "DEPLOYMENT_WITHOUT_TEST_SURFACE", "contribution": 50})
    if source_files and execution_surfaces == 0:
        score += 25
        reasons.append({"kind": "SOURCE_WITHOUT_DETECTED_EXECUTION_SURFACE", "contribution": 25})
    if data.get("sensitive_named_paths"):
        # Presence of a sensitive-looking filename is not a defect and must not
        # raise priority. Preserve it only as an explicit review note.
        reasons.append({
            "kind": "SENSITIVE_NAMED_PATH_REVIEW",
            "count": len(data["sensitive_named_paths"]),
            "contribution": 0,
        })
    if data.get("status") != "CONTENT_SCANNED":
        score += 1000
        reasons.append({"kind": "CONTENT_SCAN_INCOMPLETE", "contribution": 1000})

    reasons.sort(key=lambda item: (-int(item.get("contribution", 0)), str(item.get("path", "")), str(item.get("marker", ""))))
    return {
        "repository": data["repository"],
        "head_sha": data.get("head_sha"),
        "score": score,
        "status": data.get("status"),
        "source_files": source_files,
        "test_files": test_files,
        "deployment_files": deployment_files,
        "execution_surfaces": execution_surfaces,
        "source_marker_counts": dict(sorted(source_marker_counts.items())),
        "non_source_marker_counts": dict(sorted(non_source_marker_counts.items())),
        "reasons": reasons[:100],
        "content_evidence_digest": data.get("evidence_digest"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-dir", default="crystallization/content-index")
    parser.add_argument("--index", default="crystallization/public-content-index.json")
    parser.add_argument("--output", default="crystallization/content-priority-queue.json")
    args = parser.parse_args()
    index = json.loads(Path(args.index).read_text())
    if index.get("content_scan_complete") is not True:
        raise SystemExit("refusing priority queue from incomplete content scan")
    manifests = []
    for path in sorted(Path(args.content_dir).glob("GlacierEQ__*.json")):
        manifests.append(json.loads(path.read_text()))
    if len(manifests) != index["repository_count"]:
        raise SystemExit(f"manifest_count_mismatch:{len(manifests)}!={index['repository_count']}")
    ranked = [score_manifest(data) for data in manifests]
    ranked.sort(key=lambda item: (-item["score"], item["repository"].lower()))
    result = {
        "schema": "glaciereq.crystallization.content-priority-queue.v1",
        "scope": index["scope"],
        "source_content_index_digest": index["index_digest"],
        "repository_count": len(ranked),
        "completion_claim_allowed": False,
        "ranking_rule": "Executable-source unfinished/fake-runtime evidence dominates documentation markers. Structural gaps add pressure but never prove BROKEN alone.",
        "repositories": ranked,
    }
    result["queue_digest"] = sha(result)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": "PASS",
        "repository_count": len(ranked),
        "queue_digest": result["queue_digest"],
        "top": [{"repository": item["repository"], "score": item["score"]} for item in ranked[:20]],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
