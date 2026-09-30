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
