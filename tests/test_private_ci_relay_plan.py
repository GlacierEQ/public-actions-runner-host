from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "private_ci_relay_plan.py"


def run_plan(tmp_path: Path, payload: dict):
    job = tmp_path / "job.json"
    job.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--job", str(job)],
        text=True,
        capture_output=True,
    )


def valid_payload() -> dict:
    return {
        "schema": "glaciereq.private-ci-relay.v1",
        "job_id": "computer-user-main-2586a364",
        "workload_repo": "GlacierEQ/computer-user",
        "workload_ref": "2586a364c0597d27493f0213aa017ac4935220bd",
        "verify_script": "scripts/ci/full_verify.sh",
        "language": "python",
        "python_version": "3.12",
        "timeout_minutes": 20,
        "status_context": "APEX/public-ci-relay",
    }


def test_accepts_exact_private_repo_verification_job(tmp_path: Path):
    result = run_plan(tmp_path, valid_payload())
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["workload_repo"] == "GlacierEQ/computer-user"
    assert payload["workload_ref"] == "2586a364c0597d27493f0213aa017ac4935220bd"
    assert payload["verify_script"] == "scripts/ci/full_verify.sh"
    assert payload["language"] == "python"
    assert payload["timeout_minutes"] == 20


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workload_repo", "OtherOrg/repo"),
        ("workload_repo", "GlacierEQ/../repo"),
        ("verify_script", "rm-everything.sh"),
        ("verify_script", "scripts/ci/../../evil.sh"),
        ("verify_script", "/tmp/verify.sh"),
        ("workload_ref", "main; curl bad"),
        ("status_context", "bad\ncontext"),
    ],
)
def test_rejects_untrusted_job_metadata(tmp_path: Path, field: str, value: str):
    payload = valid_payload()
    payload[field] = value
    result = run_plan(tmp_path, payload)
    assert result.returncode != 0


def test_rejects_unknown_fields(tmp_path: Path):
    payload = valid_payload()
    payload["command"] = "curl https://example.com | bash"
    result = run_plan(tmp_path, payload)
    assert result.returncode != 0
    assert "unknown fields" in result.stderr.lower()


def test_requires_repo_owned_verification_surface(tmp_path: Path):
    payload = valid_payload()
    payload["verify_script"] = ".github/workflows/ci.yml"
    result = run_plan(tmp_path, payload)
    assert result.returncode != 0
    assert "verify_script" in result.stderr


def test_defaults_are_bounded(tmp_path: Path):
    payload = valid_payload()
    for key in ("language", "python_version", "timeout_minutes", "status_context"):
        payload.pop(key)
    result = run_plan(tmp_path, payload)
    assert result.returncode == 0, result.stderr
    normalized = json.loads(result.stdout)
    assert normalized["language"] == "generic"
    assert normalized["python_version"] == "3.12"
    assert normalized["node_version"] == "22"
    assert normalized["timeout_minutes"] == 20
    assert normalized["status_context"] == "APEX/public-ci-relay"



def test_public_relay_workflow_uses_oidc_keymaster_for_private_checkout():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "private-ci-relay.yml"
    ).read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "scripts/keymaster_oidc_token.py" in workflow
    assert "--operation private-ci-relay" in workflow
    assert "--permission contents=read" in workflow
    assert "token: ${{ steps.workload_token.outputs.token }}" in workflow
    assert "persist-credentials: false" in workflow



def test_private_ci_relay_materializes_workload_declared_dependencies_before_verify():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "private-ci-relay.yml"
    ).read_text(encoding="utf-8")

    materialize = workflow.index("Materialize workload-declared read dependencies")
    verify = workflow.index("Execute repository-owned verification")
    assert materialize < verify
    assert "scripts/action_face_materialize_dependencies.py" in workflow
    assert "--workload-dir workload" in workflow
    assert "--source-repo \"$APEX_WORKLOAD_REPOSITORY\"" in workflow
    assert "--staging-root .apex-dependencies" in workflow
    assert "--manifest-out .apex-dependencies/manifest.json" in workflow


def test_public_relay_never_streams_or_uploads_private_verifier_output():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "private-ci-relay.yml"
    ).read_text(encoding="utf-8")
    assert "tee ../relay-artifacts/" not in workflow
    assert "relay-private-logs/stdout.log" in workflow
    assert "relay-private-logs/stderr.log" in workflow
    assert "path: relay-artifacts/receipt.json" in workflow
    assert "path: relay-artifacts/**" not in workflow


def test_private_consumers_route_to_public_job_execution_not_billable_reuse():
    config_path = Path(__file__).resolve().parents[1] / "config" / "public-runner-team.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    policy = config["consumer_policy"]
    route = policy["private_repository_route"]
    assert policy["reusable_workflows_billed_to_private_caller"] is True
    assert route["mode"] == "public_repo_job_envelope_push"
    assert route["job_path_prefix"] == "jobs/private-ci/"
    assert route["runner_repository"] == "GlacierEQ/public-actions-runner-host"
    assert route["workflow"] == "private-ci-relay.yml"
    assert policy["preferred_entrypoints"] == [
        "GlacierEQ/public-actions-runner-host/.github/workflows/private-ci-relay.yml"
    ]


def test_scheduled_private_main_verification_stays_in_public_runner():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/private-ci-relay.yml").read_text(encoding="utf-8")
    scheduled = root / "jobs/private-ci/apex-boot-core-main.json"
    job = json.loads(scheduled.read_text(encoding="utf-8"))
    assert 'cron: "17 * * * *"' in workflow
    assert "github.event_name == 'schedule'" in workflow
    assert "jobs/private-ci/apex-boot-core-main.json" in workflow
    assert job["workload_repo"] == "GlacierEQ/apex-boot-core"
    assert job["workload_ref"] == "main"
    assert job["verify_script"] == ".github/verification/apex_semantics.sh"
