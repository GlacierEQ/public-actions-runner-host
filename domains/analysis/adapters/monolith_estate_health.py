"""Produce bounded Monolith estate-health findings from committed evidence surfaces."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import apex_catalog_runner as catalog

EXPECTED_ACTION = "analysis.monolith.estate-health"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "audit"
SHA = re.compile(r"^[0-9a-f]{40}$")


def validate_plan(plan: dict) -> None:
    expected = {
        "pillar": "D",
        "action": EXPECTED_ACTION,
        "adapter": EXPECTED_ADAPTER,
        "task": "audit",
        "source_repo": EXPECTED_REPOSITORY,
        "target_repo": EXPECTED_REPOSITORY,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise ValueError(f"{field} identity mismatch")


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid analysis source: {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"analysis source must be an object: {path.name}")
    return value


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    try:
        validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA is unavailable or invalid",
        )

    atlas_path = workspace / "catalog" / "monolith_command_atlas.json"
    library_path = workspace / "catalog" / "library.json"
    try:
        atlas = read_object(atlas_path)
        library = read_object(library_path)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    estate = atlas.get("estate")
    dispositions = atlas.get("disposition_counts")
    actions = atlas.get("action_queue")
    systems = atlas.get("systems")
    scale_notes = library.get("scale_notes")
    if not all(
        (
            isinstance(estate, dict),
            isinstance(dispositions, dict),
            isinstance(actions, list),
            isinstance(systems, list),
            isinstance(scale_notes, dict),
        )
    ):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="Monolith estate surfaces do not satisfy the analysis contract",
        )

    catalog_entries = int(estate.get("catalog_entries", 0))
    inspected = int(estate.get("source_inspected_repositories", 0))
    inspected_percent = float(estate.get("source_inspected_percent", 0))
    verified_edges = int(estate.get("verified_integration_edges", 0))
    relationship_edges = int(estate.get("relationship_edges", 0))
    lag_days = int(estate.get("catalog_to_evidence_lag_days", 0))

    findings: list[dict[str, object]] = []
    if inspected_percent < 10:
        findings.append(
            {
                "severity": "high",
                "signal": "source_inspection_coverage",
                "value": inspected_percent,
                "threshold": 10,
                "recommended_action": "Expand fact cards across mapped wholes and worked-on repositories.",
            }
        )
    if relationship_edges and verified_edges == 0:
        findings.append(
            {
                "severity": "high",
                "signal": "verified_integrations",
                "value": verified_edges,
                "relationship_edges": relationship_edges,
                "recommended_action": "Produce exact integration receipts before promoting relationship edges.",
            }
        )
    if lag_days > 3:
        findings.append(
            {
                "severity": "medium",
                "signal": "catalog_evidence_lag_days",
                "value": lag_days,
                "threshold": 3,
                "recommended_action": "Regenerate the catalog and evidence surfaces from one pinned source state.",
            }
        )
    if bool(scale_notes.get("incomplete_mirror")):
        findings.append(
            {
                "severity": "bounded",
                "signal": "incomplete_mirror",
                "value": True,
                "recommended_action": "Preserve the snapshot limitation in every estate-total claim.",
            }
        )

    priority_counts: dict[str, int] = {}
    for item in actions:
        if isinstance(item, dict):
            priority = str(item.get("priority", "unknown"))
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

    status = "completed" if catalog_entries > 0 and inspected <= catalog_entries else "failed"
    return catalog.write_result(
        plan,
        result_path,
        status,
        estate_health={
            "catalog_entries": catalog_entries,
            "source_inspected_repositories": inspected,
            "source_inspected_percent": inspected_percent,
            "relationship_edges": relationship_edges,
            "verified_integration_edges": verified_edges,
            "catalog_to_evidence_lag_days": lag_days,
            "system_count": len(systems),
            "action_count": len(actions),
            "priority_counts": priority_counts,
            "disposition_counts": dispositions,
            "finding_count": len(findings),
        },
        findings=findings[:25],
    )
