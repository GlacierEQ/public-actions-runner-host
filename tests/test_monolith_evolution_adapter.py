from __future__ import annotations

import base64
import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import action_face_plan as planner
from monolith_evolution_adapter import (
    isolated_env,
    parse_test_count,
    seal_artifact,
    validate_ledger,
    validate_markdown,
)


def sample_source_catalog() -> dict:
    return {
        "version": 2,
        "updated": "2026-07-25T11:22:58Z",
        "entries": [
            {
                "name": "alpha",
                "domain": "long_tail",
                "role": "part",
                "activity": "catalogued",
            },
            {
                "name": "beta",
                "domain": "mcp_connectors",
                "role": "part",
                "activity": "worked_on",
            },
        ],
    }


def sample_ledger() -> dict:
    return {
        "source_catalog": {
            "path": "catalog/library.json",
            "version": 2,
            "updated": "2026-07-25T11:22:58Z",
            "entry_count": 2,
        },
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
                "role": "part",
                "activity": "catalogued",
                "function_category": "specialist",
                "generation": "G1_GENESIS",
                "generation_reason": "catalogued component",
                "system_level": "L1_COMPONENT",
                "system_level_reason": "discrete component",
            },
            {
                "name": "beta",
                "domain": "mcp_connectors",
                "role": "part",
                "activity": "worked_on",
                "function_category": "connectors",
                "generation": "G2_SPECIALIST",
                "generation_reason": "worked integration",
                "system_level": "L2_SUBSYSTEM",
                "system_level_reason": "integration evidence",
            },
        ],
    }


def test_monolith_evolution_adapter_is_preserved_but_not_executable() -> None:
    with pytest.raises(SystemExit, match="not registered"):
        planner.resolve_action("monolith-evolution-map", "D")
    assert planner.ADAPTER_TASK["monolith-evolution"] == "test"


def test_private_workload_environment_strips_trust_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = {
        "AKOS_POLICY_SHA256": "policy-secret",
        "APEX_CONTROL_TOKEN": "control-secret",
        "APEX_PRIVATE_READ_TOKEN": "read-secret",
        "GITHUB_TOKEN": "github-secret",
    }
    for key, value in protected.items():
        monkeypatch.setenv(key, value)
    env = isolated_env()
    assert protected.keys().isdisjoint(env)
    assert env["CI"] == "true"


def test_validate_ledger_binds_records_and_generation_counts() -> None:
    entry_count, generation_counts = validate_ledger(
        sample_ledger(), sample_source_catalog()
    )
    assert entry_count == 2
    assert sum(generation_counts.values()) == 2


def test_validate_ledger_rejects_generation_histogram_mismatch() -> None:
    ledger = sample_ledger()
    ledger["records"][1]["generation"] = "G1_GENESIS"
    with pytest.raises(ValueError, match="declared generation counts"):
        validate_ledger(ledger, sample_source_catalog())


def test_validate_ledger_rejects_changed_source_fields() -> None:
    ledger = sample_ledger()
    ledger["records"][0]["domain"] = "legal_case"
    with pytest.raises(ValueError, match="changed source field domain"):
        validate_ledger(ledger, sample_source_catalog())


def test_parse_test_count_rejects_missing_or_zero_tests() -> None:
    with pytest.raises(ValueError, match="did not report"):
        parse_test_count("OK")
    with pytest.raises(ValueError, match="zero tests"):
        parse_test_count("Ran 0 tests in 0.000s\nOK")
    assert parse_test_count("Ran 27 tests in 0.121s\nOK") == 27


def test_validate_markdown_requires_governed_structure() -> None:
    counts = {"G1_GENESIS": 1, "G2_SPECIALIST": 1}
    valid = (
        "# Evolution Levels\n"
        "**Repositories classified:** 2\n"
        "## Complete repository placement\n"
        "### G1_GENESIS\n"
        "### G2_SPECIALIST\n"
        "## Regenerate and verify"
    )
    validate_markdown(valid, 2, counts)
    with pytest.raises(ValueError, match="lacks required structure"):
        validate_markdown("# Evolution Levels", 2, counts)


def test_sealed_artifact_is_deterministic_and_round_trips(
    tmp_path: Path,
) -> None:
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
