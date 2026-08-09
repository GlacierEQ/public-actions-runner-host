from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import action_face_plan


def load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_retired_monolith_contracts_are_removed_from_execution_admission() -> None:
    retired = load("config/retired-action-contracts.json")
    flat = load("config/action-face-actions.json")
    retired_names = {
        name
        for contract in retired["contracts"]
        for name in contract["active_names"]
    }
    flat_names = {item["action"] for item in flat["actions"]}
    assert retired_names.isdisjoint(flat_names)


@pytest.mark.parametrize(
    ("action", "pillar"),
    (
        ("monolith-evolution-map", "D"),
        ("monolith-ip-governance", "D"),
        ("code.monolith.validate-company-engineered-registry", "C"),
    ),
)
def test_retired_monolith_routes_fail_closed_at_canonical_ingress(
    action: str, pillar: str
) -> None:
    with pytest.raises(SystemExit, match="not registered"):
        action_face_plan.resolve_action(action, pillar)


def test_monolith_recovery_keeps_current_proven_gates_active() -> None:
    flat = load("config/action-face-actions.json")
    active = {item["action"] for item in flat["actions"]}
    assert {
        "code.monolith.validate-atlases",
        "docs.monolith.validate-integrity",
        "analysis.monolith.estate-health",
        "code.monolith.validate-legal-live-reconciliation",
    } <= active


def test_retirement_records_are_evidence_bound() -> None:
    retired = load("config/retired-action-contracts.json")
    assert retired["source_checkpoint"] == "827af23d68f0856280e95e9c9c1f2571625a6ff4"
    assert len(retired["contracts"]) == 3
    for contract in retired["contracts"]:
        assert contract["classification"] == "SOURCE_CONTRACT_RETIRED"
        assert contract["recovery_job"]
        assert contract["reason"]
