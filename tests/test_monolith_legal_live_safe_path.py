from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from domains.code.adapters import monolith_legal_live_validate as adapter

SHA = "a" * 40


def write_required_files(workspace: Path) -> None:
    for relative in adapter.REQUIRED_PATHS:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            path.write_text("{}\n", encoding="utf-8")
        elif path.suffix == ".py":
            path.write_text("pass\n", encoding="utf-8")
        else:
            path.write_text("# fixture\n", encoding="utf-8")


def plan() -> dict:
    return {
        "job_id": "SafePath01",
        "pillar": "C",
        "action": adapter.EXPECTED_ACTION,
        "adapter": adapter.EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": adapter.EXPECTED_REPOSITORY,
        "source_ref": SHA,
        "target_repo": adapter.EXPECTED_REPOSITORY,
        "execution_repo": "GlacierEQ/public-actions-runner-host",
        "public_runner_sha": SHA,
    }


def test_safe_path_binds_only_exact_attested_workload_root(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    write_required_files(workspace)
    result_path = (tmp_path / "result.json").resolve()
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", SHA)
    monkeypatch.setattr(
        adapter,
        "attest_workspace",
        lambda *_: {
            "resolved_source_sha": SHA,
            "tracked_clean": True,
            "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        },
    )
    monkeypatch.setattr(
        adapter,
        "build_environment",
        lambda *_: {"PATH": "/usr/bin", "PYTHONSAFEPATH": "1"},
    )

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == workspace
        assert kwargs["env"] == {
            "PATH": "/usr/bin",
            "PYTHONSAFEPATH": "1",
            "PYTHONPATH": str(workspace),
        }
        return SimpleNamespace(returncode=0, stdout="ok\n")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    assert adapter.run(plan(), workspace, result_path) == 0
    assert calls == adapter.commands()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "completed"


def test_non_safe_path_environment_is_not_widened(tmp_path: Path) -> None:
    env = {"PATH": "/usr/bin"}
    workspace = tmp_path.resolve()

    assert adapter.bind_attested_python_root(env, workspace) == {"PATH": "/usr/bin"}
