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


def test_canonical_issue_ingress_is_read_only_to_source() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "issues: write" in text
    assert "contents: write" not in text
    assert "persist-credentials: false" in text
    assert "APEX_RUNNER_APP_CLIENT_ID" in text
    assert "APEX_RUNNER_APP_PRIVATE_KEY" in text
    assert "action_face_publish_verified.py" in text
    assert "Enforce governed release result" in text
