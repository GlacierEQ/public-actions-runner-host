from __future__ import annotations

import base64
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import action_face_plan as planner  # noqa: E402
from monolith_evolution_adapter import seal_artifact, validate_ledger  # noqa: E402


def sample_ledger() -> dict:
    return {
        "source_catalog": {"entry_count": 2},
        "counts": {
            "generation": {
                "G0_REFERENCE": 0,
                "G1_GENESIS": 1,
                "G2_SPECIALIST": 1,
            }
        },
        "records": [
            {
                "name": "alpha",
                "domain": "long_tail",
                "function_category": "specialist",
                "generation": "G1_GENESIS",
                "generation_reason": "catalogued component",
                "system_level": "L1_COMPONENT",
                "system_level_reason": "discrete component",
            },
            {
                "name": "beta",
                "domain": "mcp_connectors",
                "function_category": "connectors",
                "generation": "G2_SPECIALIST",
                "generation_reason": "worked integration",
                "system_level": "L2_SUBSYSTEM",
                "system_level_reason": "integration evidence",
            },
        ],
    }


def test_monolith_action_is_narrowly_catalogued() -> None:
    entry = planner.resolve_action("monolith-evolution-map", "D")
    assert entry is not None
    assert entry["target_repo"] == "GlacierEQ/monolith"
    assert entry["adapter"] == "monolith-evolution"
    assert planner.ADAPTER_TASK["monolith-evolution"] == "test"


def test_validate_ledger_checks_counts_and_required_coordinates() -> None:
    entry_count, generation_counts = validate_ledger(sample_ledger())
    assert entry_count == 2
    assert sum(generation_counts.values()) == 2


def test_sealed_artifact_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    payload = json.dumps(sample_ledger(), sort_keys=True).encode("utf-8")
    path = tmp_path / "catalog" / "evolution_map.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)

    first = seal_artifact(path, tmp_path)
    second = seal_artifact(path, tmp_path)

    assert first == second
    compressed = base64.b64decode(first["data_base64"])
    assert gzip.decompress(compressed) == payload
    assert first["path"] == "catalog/evolution_map.json"
    assert first["encoding"] == "gzip+base64"
