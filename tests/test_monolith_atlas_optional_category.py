from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from domains.code.adapters import monolith_atlas_validate as adapter


def test_commands_default_to_full_function_atlas_gate(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(result, "DefaultGate01")

    assert len(sequence) == 21
    assert any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert any("test_connector_fabric.py" in command for command in sequence)
    assert any("scripts/validate_memory_aspen.py" in command for command in sequence)
    assert any("test_memory_aspen.py" in command for command in sequence)
    assert any("scripts/validate_legal_case.py" in command for command in sequence)
    assert any("test_legal_case.py" in command for command in sequence)
    assert any("scripts/validate_lab_hire_atlas.py" in command for command in sequence)
    assert any("test_lab_hire_atlas.py" in command for command in sequence)
    assert any("scripts/validate_category_heads.py" in command for command in sequence)
    assert any("test_category_heads.py" in command for command in sequence)


def test_commands_support_core_only_gate(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(result, "CoreOnly01", False, False, False, False, False)

    assert len(sequence) == 11
    assert not any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert not any("test_connector_fabric.py" in command for command in sequence)
    assert not any(
        "scripts/validate_memory_aspen.py" in command for command in sequence
    )
    assert not any("test_memory_aspen.py" in command for command in sequence)
    assert not any("scripts/validate_legal_case.py" in command for command in sequence)
    assert not any("test_legal_case.py" in command for command in sequence)
    assert not any(
        "scripts/validate_category_heads.py" in command for command in sequence
    )
    assert not any("test_category_heads.py" in command for command in sequence)
    assert any("test_function_atlas.py" in command for command in sequence)


def test_commands_support_connector_without_category_gate(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(
        result, "ConnectorGate01", False, True, False, False, False
    )

    assert len(sequence) == 13
    assert any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert any("test_connector_fabric.py" in command for command in sequence)
    assert not any(
        "scripts/validate_memory_aspen.py" in command for command in sequence
    )
    assert not any(
        "scripts/validate_category_heads.py" in command for command in sequence
    )


def test_commands_support_memory_without_other_optional_gates(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(
        result, "MemoryGate01", False, False, True, False, False
    )

    assert len(sequence) == 13
    assert any("scripts/validate_memory_aspen.py" in command for command in sequence)
    assert any("test_memory_aspen.py" in command for command in sequence)
    assert not any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert not any(
        "scripts/validate_category_heads.py" in command for command in sequence
    )


def test_commands_support_legal_without_other_optional_gates(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(result, "LegalGate01", False, False, False, True, False)

    assert len(sequence) == 13
    assert any("scripts/validate_legal_case.py" in command for command in sequence)
    assert any("test_legal_case.py" in command for command in sequence)
    assert not any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert not any(
        "scripts/validate_memory_aspen.py" in command for command in sequence
    )
    assert not any(
        "scripts/validate_category_heads.py" in command for command in sequence
    )


def test_commands_support_lab_hire_without_other_optional_gates(tmp_path: Path) -> None:
    result = tmp_path / "result.json"

    sequence = adapter.commands(
        result, "LabHireGate01", False, False, False, False, True
    )

    assert len(sequence) == 13
    assert any("scripts/validate_lab_hire_atlas.py" in command for command in sequence)
    assert any("test_lab_hire_atlas.py" in command for command in sequence)
    assert not any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert not any(
        "scripts/validate_memory_aspen.py" in command for command in sequence
    )
    assert not any("scripts/validate_legal_case.py" in command for command in sequence)
    assert not any(
        "scripts/validate_category_heads.py" in command for command in sequence
    )


def test_connector_surface_state_distinguishes_absent_partial_complete(
    tmp_path: Path,
) -> None:
    assert adapter.connector_surface_state(tmp_path) == "absent"

    first = tmp_path / adapter.CONNECTOR_REQUIRED_PATHS[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("fixture\n", encoding="utf-8")
    assert adapter.connector_surface_state(tmp_path) == "partial"

    for relative in adapter.CONNECTOR_REQUIRED_PATHS[1:]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    assert adapter.connector_surface_state(tmp_path) == "complete"


def test_memory_surface_state_distinguishes_absent_partial_complete(
    tmp_path: Path,
) -> None:
    assert adapter.memory_surface_state(tmp_path) == "absent"

    first = tmp_path / adapter.MEMORY_REQUIRED_PATHS[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("fixture\n", encoding="utf-8")
    assert adapter.memory_surface_state(tmp_path) == "partial"

    for relative in adapter.MEMORY_REQUIRED_PATHS[1:]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    assert adapter.memory_surface_state(tmp_path) == "complete"


def test_legal_surface_state_distinguishes_absent_partial_complete(
    tmp_path: Path,
) -> None:
    assert adapter.legal_surface_state(tmp_path) == "absent"

    first = tmp_path / adapter.LEGAL_REQUIRED_PATHS[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("fixture\n", encoding="utf-8")
    assert adapter.legal_surface_state(tmp_path) == "partial"

    for relative in adapter.LEGAL_REQUIRED_PATHS[1:]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    assert adapter.legal_surface_state(tmp_path) == "complete"


def test_lab_hire_surface_state_distinguishes_absent_partial_complete(
    tmp_path: Path,
) -> None:
    assert adapter.lab_hire_surface_state(tmp_path) == "absent"

    first = tmp_path / adapter.LAB_HIRE_REQUIRED_PATHS[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("fixture\n", encoding="utf-8")
    assert adapter.lab_hire_surface_state(tmp_path) == "partial"

    for relative in adapter.LAB_HIRE_REQUIRED_PATHS[1:]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    assert adapter.lab_hire_surface_state(tmp_path) == "complete"


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


def _checkout_for(workspace: Path):
    class Checkout:
        proc_path = workspace
        pass_fds = ()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    return Checkout()


def _attestation() -> dict[str, object]:
    return {
        "resolved_source_sha": "a" * 40,
        "tracked_clean": True,
        "tracked_diff_sha256": "b" * 64,
        "checkout_device": 1,
        "checkout_inode": 1,
    }


def _plan(job_id: str) -> dict[str, str]:
    return {
        "job_id": job_id,
        "pillar": "C",
        "action": adapter.EXPECTED_ACTION,
        "adapter": adapter.EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": adapter.EXPECTED_REPOSITORY,
        "source_ref": "a" * 40,
        "target_repo": adapter.EXPECTED_REPOSITORY,
    }


def test_partial_connector_surface_blocks_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in adapter.CORE_REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    partial = workspace / adapter.CONNECTOR_REQUIRED_PATHS[0]
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("fixture\n", encoding="utf-8")

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(
        adapter,
        "open_checkout",
        lambda *_args, **_kwargs: _checkout_for(workspace),
    )
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter,
        "attest_checkout",
        lambda *_args, **_kwargs: _attestation(),
    )

    result_path = tmp_path / "result.json"
    assert adapter.run(_plan("PartialConnector01"), workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "partial connector-fabric surface" in payload["reason"]


def test_partial_memory_surface_blocks_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in adapter.CORE_REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    partial = workspace / adapter.MEMORY_REQUIRED_PATHS[0]
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("fixture\n", encoding="utf-8")

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(
        adapter,
        "open_checkout",
        lambda *_args, **_kwargs: _checkout_for(workspace),
    )
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter,
        "attest_checkout",
        lambda *_args, **_kwargs: _attestation(),
    )

    result_path = tmp_path / "result.json"
    assert adapter.run(_plan("PartialMemory01"), workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "partial memory-aspen surface" in payload["reason"]


def test_partial_legal_surface_blocks_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in adapter.CORE_REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    partial = workspace / adapter.LEGAL_REQUIRED_PATHS[0]
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("fixture\n", encoding="utf-8")

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(
        adapter, "open_checkout", lambda *_args, **_kwargs: _checkout_for(workspace)
    )
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter, "attest_checkout", lambda *_args, **_kwargs: _attestation()
    )

    result_path = tmp_path / "result.json"
    assert adapter.run(_plan("PartialLegal01"), workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "partial legal-case surface" in payload["reason"]


def test_partial_lab_hire_surface_blocks_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in adapter.CORE_REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    partial = workspace / adapter.LAB_HIRE_REQUIRED_PATHS[0]
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("fixture\n", encoding="utf-8")

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(
        adapter, "open_checkout", lambda *_args, **_kwargs: _checkout_for(workspace)
    )
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter, "attest_checkout", lambda *_args, **_kwargs: _attestation()
    )

    result_path = tmp_path / "result.json"
    assert adapter.run(_plan("PartialLabHire01"), workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "partial lab-hire surface" in payload["reason"]


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
    monkeypatch.setattr(
        adapter,
        "open_checkout",
        lambda *_args, **_kwargs: _checkout_for(workspace),
    )
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter,
        "attest_checkout",
        lambda *_args, **_kwargs: _attestation(),
    )

    result_path = tmp_path / "result.json"
    assert adapter.run(_plan("PartialGate01"), workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert "partial category-head surface" in payload["reason"]


def test_command_atlas_projection_repair_is_non_mutating_and_content_addressed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_path = tmp_path / "results" / "result.json"

    generator = workspace / adapter.COMMAND_ATLAS_GENERATOR
    generator.parent.mkdir(parents=True, exist_ok=True)
    generator.write_text(
        """from pathlib import Path
root = Path(__file__).resolve().parents[1]
json_out = root / 'catalog' / 'monolith_command_atlas.json'
md_out = root / 'status' / 'MONOLITH_COMMAND_ATLAS.md'
json_out.parent.mkdir(parents=True, exist_ok=True)
md_out.parent.mkdir(parents=True, exist_ok=True)
json_out.write_text('{\"fresh\": true}\\n', encoding='utf-8')
md_out.write_text('# Fresh Command Atlas\\n', encoding='utf-8')
""",
        encoding="utf-8",
    )
    for relative, content in (
        ("catalog/library.json", "{}\n"),
        ("evidence/system_maps/control_plane_orchestration.json", "{}\n"),
        (
            "evidence/repository_fact_cards/control_plane_orchestration/repo.json",
            "{}\n",
        ),
    ):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    stale_outputs = {
        "catalog/monolith_command_atlas.json": '{"stale": true}\n',
        "status/MONOLITH_COMMAND_ATLAS.md": "# Stale Command Atlas\n",
    }
    for relative, content in stale_outputs.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    repair = adapter.command_atlas_projection_repair(
        workspace,
        result_path,
        "ProjectionRepair01",
        dict(os.environ),
        "a" * 40,
    )

    assert repair["status"] == "available"
    assert repair["resolved_source_sha"] == "a" * 40
    assert repair["fact_card_count"] == 1
    assert {item["path"] for item in repair["files"]} == set(
        adapter.COMMAND_ATLAS_OUTPUTS
    )
    assert repair["total_bytes"] == sum(item["bytes"] for item in repair["files"])
    for item in repair["files"]:
        payload = item["content"].encode("utf-8")
        assert item["bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()

    contents = {item["path"]: item["content"] for item in repair["files"]}
    assert contents["catalog/monolith_command_atlas.json"] == '{"fresh": true}\n'
    assert contents["status/MONOLITH_COMMAND_ATLAS.md"] == "# Fresh Command Atlas\n"
    for relative, stale_content in stale_outputs.items():
        assert (workspace / relative).read_text(encoding="utf-8") == stale_content


def test_failed_command_atlas_check_publishes_repair_without_promoting_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for relative in adapter.CORE_REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)

    attestation = _attestation()
    monkeypatch.setattr(
        adapter,
        "open_checkout",
        lambda *_args, **_kwargs: _checkout_for(workspace),
    )
    monkeypatch.setattr(adapter, "build_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        adapter, "attest_checkout", lambda *_args, **_kwargs: attestation
    )
    monkeypatch.setattr(
        adapter,
        "commands",
        lambda *_args: [
            ["python", adapter.COMMAND_ATLAS_GENERATOR, "--check"],
        ],
    )
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="Monolith Command Atlas JSON drift detected.\n",
        ),
    )
    expected_repair = {
        "status": "available",
        "generator": adapter.COMMAND_ATLAS_GENERATOR,
        "resolved_source_sha": "a" * 40,
        "fact_card_count": 1,
        "total_bytes": 4,
        "files": [
            {
                "path": "catalog/monolith_command_atlas.json",
                "bytes": 2,
                "sha256": hashlib.sha256(b"{}").hexdigest(),
                "content": "{}",
            },
            {
                "path": "status/MONOLITH_COMMAND_ATLAS.md",
                "bytes": 2,
                "sha256": hashlib.sha256(b"#\n").hexdigest(),
                "content": "#\n",
            },
        ],
    }
    monkeypatch.setattr(
        adapter,
        "command_atlas_projection_repair",
        lambda *_args, **_kwargs: expected_repair,
    )

    result_path = tmp_path / "result.json"
    assert adapter.run(_plan("RepairPublish01"), workspace, result_path) == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["projection_repair"] == expected_repair
    assert payload["workspace_attestation"]["before"] == attestation
    assert payload["workspace_attestation"]["after"] == attestation
