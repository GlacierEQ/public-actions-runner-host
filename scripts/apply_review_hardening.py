#!/usr/bin/env python3
"""One-shot review hardening patch; removed after application."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/action_face_selftest.py",
    '''CANARY_SOURCE_SHA = "a" * 40


def run(plan: dict, workspace: Path, result_path: Path) -> int:
''',
    '''CANARY_SOURCE_SHA = "a" * 40


def workflow_step_block(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\\n"
    start = workflow.find(marker)
    if start < 0:
        return ""
    end = workflow.find("\\n      - name: ", start + len(marker))
    return workflow[start:] if end < 0 else workflow[start:end]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
''',
)
replace_once(
    "scripts/action_face_selftest.py",
    '''    missing = [item for item in required_workflow if item not in workflow]
    forbidden = [item for item in forbidden_workflow if item in workflow]
    record(
        "workflow-authority-boundary",
        bool(workflow) and not missing and not forbidden,
        f"missing={missing}; forbidden={forbidden}",
    )
''',
    '''    missing = [item for item in required_workflow if item not in workflow]
    forbidden = [item for item in forbidden_workflow if item in workflow]
    control_token_block = workflow_step_block(
        workflow, "Mint one-repository private control token"
    )
    workload_token_block = workflow_step_block(
        workflow, "Mint one-repository private workload token"
    )
    binding_failures: list[str] = []
    for label, block, required, forbidden_in_block in (
        (
            "control",
            control_token_block,
            (
                "--repository GlacierEQ/llm-runner-teams",
                "--permission contents=write",
                "--operation public-action-control",
            ),
            ("--permission contents=read",),
        ),
        (
            "workload",
            workload_token_block,
            (
                "APEX_WORKLOAD_REPOSITORY: ${{ steps.plan.outputs.source_repo }}",
                '--repository "$APEX_WORKLOAD_REPOSITORY"',
                "--permission contents=read",
                "--operation public-action-workload",
            ),
            ("--permission contents=write",),
        ),
    ):
        absent = [item for item in required if item not in block]
        present_forbidden = [item for item in forbidden_in_block if item in block]
        if absent or present_forbidden:
            binding_failures.append(
                f"{label}:missing={absent}; forbidden={present_forbidden}"
            )
    record(
        "workflow-authority-boundary",
        bool(workflow) and not missing and not forbidden and not binding_failures,
        f"missing={missing}; forbidden={forbidden}; bindings={binding_failures}",
    )
''',
)
replace_once(
    "tests/test_apex_job_ingress_exclusivity.py",
    '''def workflow_texts() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }


def test_only_canonical_workflow_owns_apex_job_issue_ingress() -> None:
''',
    '''def workflow_texts() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }


def workflow_step_block(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\\n"
    start = workflow.find(marker)
    assert start >= 0, f"missing workflow step: {name}"
    end = workflow.find("\\n      - name: ", start + len(marker))
    return workflow[start:] if end < 0 else workflow[start:end]


def test_only_canonical_workflow_owns_apex_job_issue_ingress() -> None:
''',
)
replace_once(
    "tests/test_apex_job_ingress_exclusivity.py",
    '''def test_canonical_issue_ingress_separates_source_and_receipt_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "permissions:\\n  id-token: write\\n  contents: read\\n  issues: write" in text
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
''',
    '''def test_canonical_issue_ingress_separates_source_and_receipt_authority() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "permissions:\\n  id-token: write\\n  contents: read\\n  issues: write" in text

    control = workflow_step_block(text, "Mint one-repository private control token")
    assert "--repository GlacierEQ/llm-runner-teams" in control
    assert "--permission contents=write" in control
    assert "--permission contents=read" not in control
    assert "--operation public-action-control" in control

    workload = workflow_step_block(text, "Mint one-repository private workload token")
    assert "APEX_WORKLOAD_REPOSITORY: ${{ steps.plan.outputs.source_repo }}" in workload
    assert '--repository "$APEX_WORKLOAD_REPOSITORY"' in workload
    assert "--permission contents=read" in workload
    assert "--permission contents=write" not in workload
    assert "--operation public-action-workload" in workload
    assert '--repository "${{ steps.plan.outputs.source_repo }}"' not in workload

    assert text.count("persist-credentials: false") >= 3
    assert "APEX_RUNNER_APP_CLIENT_ID" not in text
    assert "APEX_RUNNER_APP_PRIVATE_KEY" not in text
    assert "scripts/keymaster_oidc_token.py" in text
    assert "action_face_publish_verified.py" in text
    assert "Enforce governed release result" in text
''',
)
