from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domains.analysis.adapters import monolith_estate_health
from domains.code.adapters import monolith_atlas_validate
from domains.docs.adapters import monolith_docs_validate
from scripts import action_face_plan, apex_catalog_runner, workload_isolation

SHA = "a" * 40


def git(root: Path, *args: str) -> str:
    home = root.parent / "git-home"
    home.mkdir(exist_ok=True)
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()


def commit_workspace(workspace: Path, message: str = "test source") -> str:
    if not (workspace / ".git").exists():
        git(workspace, "init")
        git(workspace, "config", "user.name", "Runner Test")
        git(workspace, "config", "user.email", "runner@example.invalid")
    git(workspace, "add", "-A")
    git(workspace, "commit", "-m", message)
    return git(workspace, "rev-parse", "HEAD").lower()


def plan(
    action: str,
    pillar: str,
    adapter: str,
    task: str,
    source_sha: str = SHA,
) -> dict:
    return {
        "job_id": "RunnerJob01",
        "pillar": pillar,
        "action": action,
        "adapter": adapter,
        "task": task,
        "source_repo": "GlacierEQ/monolith",
        "source_ref": source_sha,
        "target_repo": "GlacierEQ/monolith",
        "approval_id": "",
        "workflow_run_id": "1",
        "workflow_run_attempt": "1",
        "trigger_actor": "GlacierEQ",
        "trigger_actor_id": "1",
        "event_name": "workflow_dispatch",
        "execution_repo": "GlacierEQ/public-actions-runner-host",
        "public_runner_sha": SHA,
    }


def write_required_atlas_files(workspace: Path) -> None:
    paths = {
        "scripts/validate_function_atlas.py": "print('validated')\n",
        "tests/test_function_atlas.py": "import unittest\n",
        "scripts/validate_category_heads.py": 'print("validated")\n',
        "tests/test_category_heads.py": "import unittest\n",
        "catalog/category_heads.json": "{}\n",
        "foundations/category-heads.md": "# Category Heads\n",
        "scripts/build_monolith_command_atlas.py": "print('checked')\n",
        "scripts/query_monolith.py": "print('{}')\n",
        "tests/test_monolith_command_atlas.py": "def test_one(): assert True\n",
        "tests/test_query_monolith.py": "def test_two(): assert True\n",
        "catalog/library.json": "{}\n",
        "catalog/monolith_command_atlas.json": "{}\n",
        "status/MONOLITH_COMMAND_ATLAS.md": "# Atlas\n",
    }
    for relative, content in paths.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def write_required_docs(workspace: Path) -> None:
    required = {
        "README.md": "# Home\n[Roadmap](ROADMAP.md)\n",
        "ROADMAP.md": "# Roadmap\n",
        "AGENTS.md": "# Agents\n",
        "status/MONOLITH_COMMAND_ATLAS.md": "# Atlas\n",
        "status/MONOLITH_QUERY_GUIDE.md": "# Query\n",
        "catalog/library.json": "{}\n",
        "catalog/monolith_command_atlas.json": "{}\n",
        "schemas/monolith-command-atlas.schema.json": "{}\n",
    }
    for relative, content in required.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_action_face_plans_all_three_specialized_actions(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    cases = (
        ("C", "code.monolith.validate-atlases", "test", "test"),
        ("B", "docs.monolith.validate-integrity", "validate", "validate"),
        ("D", "analysis.monolith.estate-health", "audit", "audit"),
    )
    for pillar, action, adapter, task in cases:
        resolved = action_face_plan.build_plan(
            str(event),
            {
                "job_id": f"Plan{pillar}Job01",
                "pillar": pillar,
                "action": action,
                "source_ref": SHA,
            },
        )
        assert resolved["action"] == action
        assert resolved["adapter"] == adapter
        assert resolved["task"] == task
        assert resolved["source_repo"] == "GlacierEQ/monolith"
        assert resolved["target_repo"] == "GlacierEQ/monolith"
        assert resolved["source_ref"] == SHA


def test_specialized_actions_reject_mutable_source_refs(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}\n", encoding="utf-8")
    for pillar, action in (
        ("C", "code.monolith.validate-atlases"),
        ("B", "docs.monolith.validate-integrity"),
        ("D", "analysis.monolith.estate-health"),
    ):
        with pytest.raises(
            SystemExit,
            match="requires a full lowercase commit SHA",
        ):
            action_face_plan.build_plan(
                str(event),
                {
                    "job_id": f"Mutable{pillar}Job01",
                    "pillar": pillar,
                    "action": action,
                    "source_ref": "main",
                },
            )


def test_catalog_dispatch_is_bound_to_exact_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        monolith_atlas_validate,
        "run",
        lambda *_: calls.append("code") or 0,
    )
    monkeypatch.setattr(
        monolith_docs_validate,
        "run",
        lambda *_: calls.append("docs") or 0,
    )
    monkeypatch.setattr(
        monolith_estate_health,
        "run",
        lambda *_: calls.append("analysis") or 0,
    )

    workspace = Path("workspace")
    result = Path("result.json")
    for action, pillar, adapter, task, expected in (
        ("code.monolith.validate-atlases", "C", "test", "test", "code"),
        ("docs.monolith.validate-integrity", "B", "validate", "validate", "docs"),
        ("analysis.monolith.estate-health", "D", "audit", "audit", "analysis"),
    ):
        value = apex_catalog_runner.run_registered_specialization(
            plan(action, pillar, adapter, task), workspace, result
        )
        assert value == 0
        assert calls[-1] == expected

    assert (
        apex_catalog_runner.run_registered_specialization(
            plan("not-specialized", "C", "test", "test"), workspace, result
        )
        is None
    )


def test_workload_environment_removes_ambient_runner_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = {
        "GITHUB_TOKEN": "github-secret",
        "GITHUB_OUTPUT": "/tmp/github-output",
        "GITHUB_ENV": "/tmp/github-env",
        "ACTIONS_RUNTIME_TOKEN": "actions-secret",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-secret",
        "APEX_CONTROL_TOKEN": "control-secret",
        "APEX_RUNNER_APP_PRIVATE_KEY": "private-key",
        "AKOS_POLICY_SHA256": "b" * 64,
    }
    for key, value in protected.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", "/home/runner")

    environment = workload_isolation.build_environment(
        tmp_path / "results" / "result.json",
        "IsolationJob01",
        extra={"APEX_RESOLVED_SOURCE_SHA": SHA},
    )

    assert environment["APEX_RESOLVED_SOURCE_SHA"] == SHA
    assert environment["HOME"] != "/home/runner"
    assert environment["PIP_CONFIG_FILE"] == "/dev/null"
    assert environment["NPM_CONFIG_USERCONFIG"] == "/dev/null"
    assert all(key not in environment for key in protected)

    with pytest.raises(
        workload_isolation.WorkloadIsolationError,
        match="extra workload environment key is forbidden",
    ):
        workload_isolation.build_environment(
            tmp_path / "results" / "other.json",
            "IsolationJob02",
            extra={"GITHUB_TOKEN": "forbidden"},
        )


def test_code_runner_reproduces_both_failed_monolith_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_required_atlas_files(workspace)
    source_sha = commit_workspace(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)
    real_commands = monolith_atlas_validate.commands
    monkeypatch.setattr(
        monolith_atlas_validate,
        "commands",
        lambda *_: [["/bin/true"]],
    )

    value = monolith_atlas_validate.run(
        plan(
            "code.monolith.validate-atlases",
            "C",
            "test",
            "test",
            source_sha,
        ),
        workspace,
        result_path,
    )
    assert value == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["validated_gates"] == [
        "core-function-atlas",
        "category-head-hierarchy",
        "monolith-command-atlas",
    ]
    assert result["steps"] == [
        {
            "command": ["/bin/true"],
            "exit_code": 0,
            "output_sha256": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
            "output_tail": "",
            "status": "completed",
        }
    ]
    before = result["workspace_attestation"]["before"]
    after = result["workspace_attestation"]["after"]
    assert before["resolved_source_sha"] == source_sha
    assert after["resolved_source_sha"] == source_sha
    assert before["checkout_inode"] == after["checkout_inode"]

    sequence = real_commands(result_path, "RunnerJob01")
    assert len(sequence) == 15
    assert any("scripts/validate_function_atlas.py" in command for command in sequence)
    assert any(
        "scripts/validate_connector_fabric.py" in command for command in sequence
    )
    assert any("test_connector_fabric.py" in command for command in sequence)
    assert any("scripts/validate_category_heads.py" in command for command in sequence)
    assert any("test_category_heads.py" in command for command in sequence)
    assert any(
        "scripts/build_monolith_command_atlas.py" in command for command in sequence
    )
    assert any("tests/test_query_monolith.py" in command for command in sequence)


def test_code_runner_fails_when_private_source_mutates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_required_atlas_files(workspace)
    source_sha = commit_workspace(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)
    monkeypatch.setattr(
        monolith_atlas_validate,
        "commands",
        lambda *_: [
            [
                "/bin/sh",
                "-c",
                "printf 'mutation\\n' >> scripts/validate_function_atlas.py",
            ]
        ],
    )

    value = monolith_atlas_validate.run(
        plan(
            "code.monolith.validate-atlases",
            "C",
            "test",
            "test",
            source_sha,
        ),
        workspace,
        result_path,
    )
    assert value == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["workspace_attestation"]["after"] is None
    assert result["steps"][-1]["command"] == ["workload-attestation"]
    assert "tracked workload files changed" in result["steps"][-1]["reason"]


def test_docs_runner_detects_private_structure_without_leaking_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_required_docs(workspace)
    source_sha = commit_workspace(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)

    value = monolith_docs_validate.run(
        plan(
            "docs.monolith.validate-integrity",
            "B",
            "validate",
            "validate",
            source_sha,
        ),
        workspace,
        result_path,
    )
    assert value == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["docs_summary"]["internal_links_checked"] == 1
    assert result["broken_links"] == []

    (workspace / "README.md").write_text(
        "# Home\n[Missing](private-secret-name.md)\n",
        encoding="utf-8",
    )
    source_sha = commit_workspace(workspace, "broken link")
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)
    value = monolith_docs_validate.run(
        plan(
            "docs.monolith.validate-integrity",
            "B",
            "validate",
            "validate",
            source_sha,
        ),
        workspace,
        result_path,
    )
    assert value == 2
    failed = json.loads(result_path.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["broken_links"][0]["reason"] == "target does not exist"
    assert "private-secret-name" not in json.dumps(failed)


def test_docs_runner_rejects_tracked_symlink(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_required_docs(workspace)
    outside = tmp_path / "outside.md"
    outside.write_text("private outside content\n", encoding="utf-8")
    (workspace / "leak.md").symlink_to(outside)
    source_sha = commit_workspace(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)

    value = monolith_docs_validate.run(
        plan(
            "docs.monolith.validate-integrity",
            "B",
            "validate",
            "validate",
            source_sha,
        ),
        workspace,
        result_path,
    )
    assert value == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "private outside content" not in json.dumps(result)


def test_analysis_runner_emits_bounded_health_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "catalog").mkdir(parents=True)
    (workspace / "catalog" / "monolith_command_atlas.json").write_text(
        json.dumps(
            {
                "estate": {
                    "catalog_entries": 1165,
                    "source_inspected_repositories": 10,
                    "source_inspected_percent": 0.86,
                    "verified_integration_edges": 0,
                    "relationship_edges": 18,
                    "catalog_to_evidence_lag_days": 7,
                },
                "disposition_counts": {"CORE": 1, "REPAIR": 2},
                "action_queue": [
                    {"priority": "P0"},
                    {"priority": "P0"},
                    {"priority": "P1"},
                ],
                "systems": [{"repository": "GlacierEQ/monolith"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "catalog" / "library.json").write_text(
        json.dumps({"scale_notes": {"incomplete_mirror": True}}) + "\n",
        encoding="utf-8",
    )
    source_sha = commit_workspace(workspace)
    result_path = tmp_path / "result.json"
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)

    value = monolith_estate_health.run(
        plan(
            "analysis.monolith.estate-health",
            "D",
            "audit",
            "audit",
            source_sha,
        ),
        workspace,
        result_path,
    )
    assert value == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["estate_health"]["priority_counts"] == {"P0": 2, "P1": 1}
    assert result["estate_health"]["finding_count"] == 4
    assert {finding["signal"] for finding in result["findings"]} == {
        "source_inspection_coverage",
        "verified_integrations",
        "catalog_evidence_lag_days",
        "incomplete_mirror",
    }
    assert (
        result["workspace_attestation"]["before"]["checkout_inode"]
        == (result["workspace_attestation"]["after"]["checkout_inode"])
    )


def test_specialized_adapters_reject_cross_bound_plans(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = tmp_path / "result.json"
    bad = plan("code.monolith.validate-atlases", "C", "test", "test")
    bad["target_repo"] = "GlacierEQ/other"
    assert monolith_atlas_validate.run(bad, workspace, result) == 2
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"
    assert payload["reason"] == "target_repo identity mismatch"
