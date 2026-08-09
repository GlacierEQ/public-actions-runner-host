from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_retired_monolith_contracts_cannot_remain_active() -> None:
    retired = load("config/retired-action-contracts.json")
    flat = load("config/action-face-actions.json")
    domain = load("domains/code/actions.json")
    index = load("registry/actions-index.json")

    retired_names = {
        name
        for contract in retired["contracts"]
        for name in contract["active_names"]
    }
    flat_names = {item["action"] for item in flat["actions"]}
    domain_names = set(domain["actions"])
    canonical_names = set(index["canonicalActions"])
    alias_names = set(index["aliases"])

    assert retired_names.isdisjoint(flat_names)
    assert retired_names.isdisjoint(domain_names)
    assert retired_names.isdisjoint(canonical_names)
    assert retired_names.isdisjoint(alias_names)


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
