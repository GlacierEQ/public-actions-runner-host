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


def workflow_step_block(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = workflow.find(marker)
    assert start >= 0, f"missing workflow step: {name}"
    end = workflow.find("\n      - name: ", start + len(marker))
    return workflow[start:] if end < 0 else workflow[start:end]


def test_no_workflow_owns_retired_apex_job_issue_ingress() -> None:
    assert CANONICAL.is_file()
    assert not RETIRED.exists()

    workflows = workflow_texts()
    ingress = [
        name
        for name, text in workflows.items()
        if "issues:" in text and "action_face_issue_plan.py" in text
    ]
    assert ingress == []
    canonical = CANONICAL.read_text(encoding="utf-8")
    assert "issues:" not in canonical
    assert "github.event.issue" not in canonical
    assert "action_face_issue_plan.py" not in canonical
    assert "action_face_issue_status.py" not in canonical


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


def test_supported_ingress_separates_source_and_receipt_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "permissions:\n  id-token: write\n  contents: read" in text
    assert "issues: write" not in text

    control = workflow_step_block(text, "Mint one-repository private control token")
    assert "--repository GlacierEQ/llm-runner-teams" in control
    assert "--permission contents=write" in control
    assert "--permission contents=read" not in control
    assert "--operation public-action-control" in control

    workload = workflow_step_block(text, "Mint one-repository private workload token")
    assert "APEX_WORKLOAD_REPOSITORY: ${{ steps.plan.outputs.source_repo }}" in workload
    assert '--repository "$APEX_WORKLOAD_REPOSITORY"' in workload
    assert 'permission="contents=read"' in workload
    assert 'operation="public-action-workload"' in workload
    assert 'if [ "$APEX_ACTION" = "docket-sync" ]; then' in workload
    assert workload.count('permission="contents=write"') == 1
    assert 'operation="jefs-docket-acquisition"' in workload
    assert '--permission "$permission"' in workload
    assert '--operation "$operation"' in workload
    assert '--repository "${{ steps.plan.outputs.source_repo }}"' not in workload

    checkout = workflow_step_block(
        text, "Checkout catalog-approved workload without persisting credentials"
    )
    assert "APEX_WORKLOAD_TOKEN: ${{ steps.workload_token.outputs.token }}" in checkout
    assert "scripts/action_face_checkout_workload.py" in checkout
    assert "--workspace workload" in checkout
    assert "uses: actions/checkout" not in checkout

    assert text.count("persist-credentials: false") >= 2
    assert "APEX_RUNNER_APP_CLIENT_ID" not in text
    assert "APEX_RUNNER_APP_PRIVATE_KEY" not in text
    assert "scripts/keymaster_oidc_token.py" in text
    assert "action_face_publish_verified.py" in text
    assert "Enforce governed release result" in text


def test_keymaster_tokens_are_explicitly_revoked_before_release_completion() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    workload_revoke = workflow_step_block(text, "Revoke private workload token")
    control_revoke = workflow_step_block(text, "Revoke private control token")
    for block, source_step in (
        (workload_revoke, "steps.workload_token.outputs.token"),
        (control_revoke, "steps.control_token.outputs.token"),
    ):
        assert "if: always()" in block
        assert "continue-on-error: true" in block
        assert f"GITHUB_INSTALLATION_TOKEN: ${{{{ {source_step} }}}}" in block
        assert "scripts/revoke_github_installation_token.py" in block

    enforce = workflow_step_block(text, "Enforce governed release result")
    assert "steps.workload_token_revoke.outcome != 'success'" in enforce
    assert "steps.control_token_revoke.outcome != 'success'" in enforce


def test_workload_secret_and_postrun_boundaries_are_explicit() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "steps.plan.outputs.action == 'akos-echo-policy-ci'" in text
    assert "AKOS_POLICY_SHA256: ${{ secrets.AKOS_POLICY_SHA256 }}" not in text
    assert "Verify post-run control, workload, and result integrity" in text
    assert "--workload-root workload" in text
    assert "steps.postrun_guard.outcome == 'failure'" in text
    assert "steps.synthesize.outputs.synthesized == 'true'" in text
