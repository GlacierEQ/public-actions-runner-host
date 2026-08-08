from __future__ import annotations

import json
from pathlib import Path

from domains.code.adapters import monolith_atlas_validate as adapter


def test_commands_default_to_full_category_gate(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(result, "DefaultGate01")

    assert len(sequence) == 13
    assert any("scripts/validate_category_heads.py" in command for command in sequence)
    assert any("test_category_heads.py" in command for command in sequence)


def test_commands_support_core_only_gate(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(result, "CoreOnly01", False)

    assert len(sequence) == 11
    assert not any("scripts/validate_category_heads.py" in command for command in sequence)
    assert not any("test_category_heads.py" in command for command in sequence)
    assert any("test_function_atlas.py" in command for command in sequence)


def test_category_surface_state_distinguishes_absent_partial_complete(
    tmp_path: Path,
) -> None:
    assert adapter.category_surface_state(tmp_path) == "absent"

    first = tmp_path / adapter.CATEGORY_REQUIRED_PATHS[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("fixture\n", encoding="utf-8")
    assert adapter.category_surface_state(tmp_path) == "partial"

    for relative in adapter.CATEGORY_REQUIRED_PATHS[1:]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    assert adapter.category_surface_state(tmp_path) == "complete"


def test_partial_category_surface_blocks_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in adapter.CORE_REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    partial = workspace / adapter.CATEGORY_REQUIRED_PATHS[0]
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("fixture\n", encoding="utf-8")

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)

    class Checkout:
        proc_path = workspace
        pass_fds = ()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(adapter, "open_checkout", lambda *_args, **_kwargs: Checkout())
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter,
        "attest_checkout",
        lambda *_args, **_kwargs: {
            "resolved_source_sha": "a" * 40,
            "tracked_clean": True,
            "tracked_diff_sha256": "b" * 64,
            "checkout_device": 1,
            "checkout_inode": 1,
        },
    )

    plan = {
        "job_id": "PartialGate01",
        "pillar": "C",
        "action": adapter.EXPECTED_ACTION,
        "adapter": adapter.EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": adapter.EXPECTED_REPOSITORY,
        "source_ref": "a" * 40,
        "target_repo": adapter.EXPECTED_REPOSITORY,
    }
    result_path = tmp_path / "result.json"

    assert adapter.run(plan, workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "partial category-head surface" in payload["reason"]
