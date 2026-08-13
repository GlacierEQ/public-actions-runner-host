from __future__ import annotations

import json
from pathlib import Path

from scripts import action_face_catalog_runner as runner


def test_coordinator_action_uses_repository_owned_verifier() -> None:
    catalog = json.loads(Path("config/action-face-actions.json").read_text(encoding="utf-8"))
    action = next(
        item
        for item in catalog["actions"]
        if item["action"] == "anthropic-agent-coordinator-ci"
    )
    assert action["target_repo"] == "GlacierEQ/anthropic-agent-coordinator"
    assert action["adapter"] == "constellation-memory-verify"


def test_repository_owned_verifier_binds_resolved_sha(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workload"
    script = workspace / "scripts" / "ci" / "verify.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    result_path = tmp_path / "result.json"
    source_sha = "a" * 40
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", source_sha)

    observed = {}

    def fake_run_sequence(plan, actual_workspace, actual_result, commands, **kwargs):
        observed["plan"] = plan
        observed["workspace"] = actual_workspace
        observed["result"] = actual_result
        observed["commands"] = commands
        observed["extra_env"] = kwargs.get("extra_env")
        return 0

    monkeypatch.setattr(runner, "run_sequence", fake_run_sequence)
    plan = {
        "job_id": "coordinator-proof-test",
        "source_repo": "GlacierEQ/anthropic-agent-coordinator",
    }

    assert runner.constellation_memory_verify(plan, workspace, result_path) == 0
    assert observed["commands"] == [["bash", "scripts/ci/verify.sh"]]
    assert observed["extra_env"] == {
        "GITHUB_REPOSITORY": "GlacierEQ/anthropic-agent-coordinator",
        "GITHUB_SHA": source_sha,
    }


def test_repository_owned_verifier_fails_closed_without_exact_sha(
    tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "workload"
    script = workspace / "scripts" / "ci" / "verify.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    result_path = tmp_path / "result.json"
    monkeypatch.delenv("APEX_RESOLVED_SOURCE_SHA", raising=False)

    plan = {
        "job_id": "coordinator-proof-test",
        "source_repo": "GlacierEQ/anthropic-agent-coordinator",
    }
    assert runner.constellation_memory_verify(plan, workspace, result_path) != 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked"
    assert "resolved source SHA" in result["reason"]
