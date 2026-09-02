from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import action_face_plan


UDC_SHA = "8bbf1c7751ec0ebbdcc4f98d6fc48f3adb00c048"


def empty_event(tmp_path: Path) -> Path:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({}) + "\n", encoding="utf-8")
    return event


def test_unique_catalog_action_infers_pillar(tmp_path: Path) -> None:
    plan = action_face_plan.build_plan(
        str(empty_event(tmp_path)),
        {
            "job_id": "udc-bridge-ci-test-01",
            "action": "udc-supabase-bridge-ci",
            "source_ref": UDC_SHA,
        },
    )
    assert plan["pillar"] == "C"
    assert plan["source_repo"] == "GlacierEQ/UDC"
    assert plan["adapter"] == "node-ci"
    assert plan["task"] == "test"
    assert plan["source_ref"] == UDC_SHA


def test_udc_action_requires_exact_sha(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="requires a full lowercase commit SHA"):
        action_face_plan.build_plan(
            str(empty_event(tmp_path)),
            {
                "job_id": "udc-bridge-ci-test-02",
                "action": "udc-supabase-bridge-ci",
                "source_ref": "feat/supabase-local-agent-bridge-v1",
            },
        )


def test_explicit_pillar_still_must_match_catalog(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="action is not registered to the requested pillar"):
        action_face_plan.build_plan(
            str(empty_event(tmp_path)),
            {
                "job_id": "udc-bridge-ci-test-03",
                "pillar": "F",
                "action": "udc-supabase-bridge-ci",
                "source_ref": UDC_SHA,
            },
        )
