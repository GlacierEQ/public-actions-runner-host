from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "action_face_plan.py"


def _module():
    spec = importlib.util.spec_from_file_location("action_face_plan_operator_authority", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(tmp_path: Path) -> str:
    path = tmp_path / "event.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


def test_legal_pillar_does_not_create_blanket_approval_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    plan = _module().build_plan(
        _event(tmp_path),
        {"job_id": "case-matrix-probe", "pillar": "G", "action": "case-matrix"},
    )

    assert plan["pillar"] == "G"
    assert plan["approval_required"] == "false"
    assert plan["approval_id"] == ""


def test_international_pillar_does_not_create_blanket_approval_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    plan = _module().build_plan(
        _event(tmp_path),
        {
            "job_id": "intl-package-probe",
            "pillar": "I",
            "action": "hague-package",
        },
    )

    assert plan["pillar"] == "I"
    assert plan["approval_required"] == "false"


def test_explicit_destructive_action_still_requires_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    module = _module()

    try:
        module.build_plan(
            _event(tmp_path),
            {
                "job_id": "strand-extinction-probe",
                "pillar": "F",
                "action": "master-strand-extinction",
            },
        )
    except SystemExit as exc:
        assert "requires a valid private approval_id" in str(exc)
    else:
        raise AssertionError("explicit destructive action must require approval")


def test_planner_never_infers_approval_from_pillar_category():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'pillar in {"G", "I"}' not in source
    assert 'approval_required = bool(entry and entry.get("approval_required"))' in source

    catalog = json.loads((ROOT / "config" / "action-face-actions.json").read_text(encoding="utf-8"))
    extinction = next(
        item for item in catalog["actions"] if item["action"] == "master-strand-extinction"
    )
    assert extinction["approval_required"] is True
