from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CANONICAL = WORKFLOWS / "apex-pillar-runner.yml"
RETIRED = WORKFLOWS / "apex-intelligent-issue-resolver.yml"


def workflow_texts() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }


def test_only_canonical_workflow_owns_apex_job_issue_ingress() -> None:
    assert CANONICAL.is_file()
    assert not RETIRED.exists()

    workflows = workflow_texts()
    ingress = [
        name
        for name, text in workflows.items()
        if "issues:" in text and "action_face_issue_plan.py" in text
    ]
    assert ingress == ["apex-pillar-runner.yml"]


def test_no_workflow_contains_retired_fail_open_executor() -> None:
    forbidden = (
        "APEX Intelligent Issue Resolver",
        "Parse APEX JOB Issue",
        "spec.source_repo || context.repo.owner",
        "UNKNOWN TASK: $TASK — marking complete",
        "No test files found — skipping (pass).",
    )
    for name, text in workflow_texts().items():
        for marker in forbidden:
            assert marker not in text, (
                f"{name} retains retired executor marker: {marker}"
            )


def test_canonical_issue_ingress_separates_source_and_receipt_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "permissions:\n  id-token: write\n  contents: read\n  issues: write" in text
    assert "Mint one-repository private control token" in text
    assert "--repository GlacierEQ/llm-runner-teams" in text
    assert "--permission contents=write" in text
    assert "Mint one-repository private workload token" in text
    assert '--repository "${{ steps.plan.outputs.source_repo }}"' in text
    assert "--permission contents=read" in text
    assert text.count("persist-credentials: false") >= 3
    assert "APEX_RUNNER_APP_CLIENT_ID" not in text
    assert "APEX_RUNNER_APP_PRIVATE_KEY" not in text
    assert "scripts/keymaster_oidc_token.py" in text
    assert "action_face_publish_verified.py" in text
    assert "Enforce governed release result" in text


def test_workload_secret_and_postrun_boundaries_are_explicit() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "steps.plan.outputs.action == 'akos-echo-policy-ci'" in text
    assert "AKOS_POLICY_SHA256: ${{ secrets.AKOS_POLICY_SHA256 }}" not in text
    assert "Verify post-run control, workload, and result integrity" in text
    assert "--workload-root workload" in text
    assert "steps.postrun_guard.outcome == 'failure'" in text
    assert "steps.synthesize.outputs.synthesized == 'true'" in text
