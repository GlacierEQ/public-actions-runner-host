#!/usr/bin/env python3
"""Security and contract canary for the APEX public action face."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import apex_catalog_runner as catalog

CHECKOUT_PIN = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
APP_TOKEN_PIN = (
    "actions/create-github-app-token@"
    "bcd2ba49218906704ab6c1aa796996da409d3eb1"
)
SENSITIVE_ENV = {
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "APEX_RUNNER_APP_PRIVATE_KEY",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
    "GH_PAT",
    "GITHUB_TOKEN",
}
EXPECTED_ENVELOPE_FIELDS = {
    "job_id",
    "pillar",
    "action",
    "source_repo",
    "source_ref",
    "task",
    "approval_id",
}
SPECIALIZED_ACTIONS = (
    ("C", "code.monolith.validate-atlases", "canary-code-monolith-001"),
    ("B", "docs.monolith.validate-integrity", "canary-docs-monolith-001"),
    ("D", "analysis.monolith.estate-health", "canary-analysis-monolith-001"),
)
CANARY_SOURCE_SHA = "a" * 40


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    checks: list[dict[str, str]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        checks.append(
            {
                "name": name,
                "status": "pass" if passed else "fail",
                "detail": detail[:500],
            }
        )

    leaked = sorted(key for key in SENSITIVE_ENV if os.environ.get(key))
    record(
        "control-secret-isolation",
        not leaked,
        "no protected token names are populated"
        if not leaked
        else f"unexpected variables: {', '.join(leaked)}",
    )

    script_files = sorted((workspace / "scripts").glob("*.py"))
    syntax_failures: list[str] = []
    for path in script_files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as error:
            syntax_failures.append(f"{path.name}:{error.lineno}")
    record(
        "python-syntax",
        bool(script_files) and not syntax_failures,
        ", ".join(syntax_failures) or f"{len(script_files)} scripts compiled",
    )

    json_paths = [
        *(workspace / "config").glob("*.json"),
        *(workspace / "registry").glob("*.json"),
        *(workspace / "domains").glob("*/actions.json"),
        *(workspace / "domains").glob("*/token-profiles.json"),
        *(workspace / "domains").glob("*/schemas/*.json"),
    ]
    json_failures: list[str] = []
    parsed_config: dict[str, object] = {}
    for path in sorted(set(json_paths)):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if path.parent.name == "config":
                parsed_config[path.name] = value
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            json_failures.append(f"{path.relative_to(workspace)}:{type(error).__name__}")
    record(
        "json-contracts",
        bool(json_paths) and not json_failures,
        ", ".join(json_failures) or f"{len(set(json_paths))} JSON files parsed",
    )

    schema = parsed_config.get("job-envelope.schema.json")
    schema_fields = (
        set(schema.get("properties", {})) if isinstance(schema, dict) else set()
    )
    record(
        "schema-field-alignment",
        schema_fields == EXPECTED_ENVELOPE_FIELDS,
        f"fields={sorted(schema_fields)}",
    )

    workflow_path = workspace / ".github" / "workflows" / "apex-pillar-runner.yml"
    workflow = (
        workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    )
    required_workflow = [
        "name: APEX Public Action Face",
        "runs-on: ubuntu-latest",
        "scripts/action_face_authorize.py",
        "scripts/action_face_guard.py",
        "scripts/action_face_control_plane_guard.py",
        "scripts/action_face_bind_checkout.py",
        ".apex-postrun-control/scripts/action_face_pipeline_result.py",
        ".apex-postrun-control/scripts/action_face_postrun_guard.py",
        ".apex-postrun-control/scripts/action_face_publish_verified.py",
        "--workload-root workload",
        "Verify post-run control, workload, and result integrity",
        "steps.plan.outputs.action == 'akos-echo-policy-ci'",
        "permission-contents: read",
        "permission-contents: write",
        "persist-credentials: false",
        "steps.synthesize.outputs.synthesized == 'true'",
        CHECKOUT_PIN,
        APP_TOKEN_PIN,
    ]
    forbidden_workflow = [
        "runs-on: self-hosted",
        "secrets.GH_PAT",
        "actions/github-script@",
        "actions/checkout@v",
        "actions/create-github-app-token@v",
        "AKOS_POLICY_SHA256: ${{ secrets.AKOS_POLICY_SHA256 }}",
        "python3 scripts/apex_pillar_runner.py publish",
    ]
    missing = [item for item in required_workflow if item not in workflow]
    forbidden = [item for item in forbidden_workflow if item in workflow]
    record(
        "workflow-authority-boundary",
        bool(workflow) and not missing and not forbidden,
        f"missing={missing}; forbidden={forbidden}",
    )

    retired = (
        workspace
        / ".github"
        / "workflows"
        / "apex-intelligent-issue-resolver.yml"
    )
    issue_ingress = [
        path.name
        for path in sorted((workspace / ".github" / "workflows").glob("*.y*ml"))
        if "issues:" in path.read_text(encoding="utf-8")
        and "action_face_issue_plan.py" in path.read_text(encoding="utf-8")
    ]
    record(
        "single-issue-ingress",
        not retired.exists() and issue_ingress == ["apex-pillar-runner.yml"],
        f"ingress={issue_ingress}",
    )

    isolation = (workspace / "scripts" / "workload_isolation.py").read_text(
        encoding="utf-8"
    )
    isolation_required = [
        "SAFE_EXTRA_ENV = {\"APEX_RESOLVED_SOURCE_SHA\"}",
        '"GITHUB_TOKEN"',
        '"ACTIONS_RUNTIME_TOKEN"',
        '"ACTIONS_ID_TOKEN_REQUEST_TOKEN"',
        '"PIP_CONFIG_FILE": os.devnull',
        '"NPM_CONFIG_USERCONFIG": os.devnull',
        "tracked workload files changed during execution",
        "workload HEAD changed after checkout binding",
        "path contains a symlink component",
        "checkout escapes its allowed parent",
        "secure_checkout_path",
    ]
    isolation_missing = [item for item in isolation_required if item not in isolation]
    isolation_forbidden = [
        item for item in ("os.environ.copy()", "env=os.environ") if item in isolation
    ]
    record(
        "workload-capability-minimization",
        not isolation_missing and not isolation_forbidden,
        f"missing={isolation_missing}; forbidden={isolation_forbidden}",
    )

    planner = (workspace / "scripts" / "action_face_plan.py").read_text(
        encoding="utf-8"
    )
    planner_required = [
        "resolve_domain_action",
        "IMMUTABLE_SOURCE_ACTIONS",
        "this specialized action requires a full lowercase commit SHA",
        "flat catalog and domain registry disagree",
        "hierarchical action token profile exceeds contents:read",
        "hierarchical action source writes are not forbidden",
    ]
    planner_missing = [item for item in planner_required if item not in planner]
    record(
        "runtime-registry-reconciliation",
        not planner_missing,
        f"missing={planner_missing}",
    )

    postrun = (workspace / "scripts" / "action_face_postrun_guard.py").read_text(
        encoding="utf-8"
    )
    postrun_required = [
        "--workload-root",
        "secure_checkout_path",
        "allowed_parent=runner_root",
        "verify_checkout(runner_root, workflow_sha",
        "verify_checkout(control_root, workflow_sha",
        "verify_checkout(\n            workload_root",
        "private workload",
        'output("workload_verified", "true")',
        "current plan does not match the immutable claim hash",
        "result resolved source SHA does not match checkout binding",
        'output("result_file_sha256", digest)',
    ]
    postrun_missing = [item for item in postrun_required if item not in postrun]
    record(
        "postrun-source-and-receipt-attestation",
        not postrun_missing,
        f"missing={postrun_missing}",
    )

    verified_publish = (
        workspace / "scripts" / "action_face_publish_verified.py"
    ).read_text(encoding="utf-8")
    publish_required = [
        "result bytes changed after post-run verification",
        "NamedTemporaryFile",
        "os.chmod(handle.name, 0o600)",
        "base.publish(args.job_id, Path(temporary_path))",
    ]
    publish_missing = [
        item for item in publish_required if item not in verified_publish
    ]
    record(
        "verified-publication",
        not publish_missing,
        f"missing={publish_missing}",
    )

    catalog_entries: list[dict] = []
    for name in ("pillar-actions.json", "action-face-actions.json"):
        value = parsed_config.get(name)
        if isinstance(value, dict):
            entries = value.get("actions", [])
            if isinstance(entries, list):
                catalog_entries.extend(
                    item for item in entries if isinstance(item, dict)
                )
    keys = [(item.get("pillar"), item.get("action")) for item in catalog_entries]
    targets = [str(item.get("target_repo", "")) for item in catalog_entries]
    catalog_ok = (
        bool(keys)
        and len(keys) == len(set(keys))
        and all(target.startswith("GlacierEQ/") for target in targets)
    )
    record("catalog-uniqueness", catalog_ok, f"{len(keys)} actions checked")

    with tempfile.TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        planner_output = temporary_path / "planner-output.txt"
        auth_output = temporary_path / "auth-output.txt"
        valid_event = temporary_path / "valid.json"
        invalid_event = temporary_path / "invalid.json"
        manual_event = temporary_path / "manual.json"
        manual_event.write_text("{}\n", encoding="utf-8")
        valid_event.write_text(
            json.dumps(
                {
                    "action": "action-face-canary",
                    "client_payload": {
                        "job_id": "canary-20260716-001",
                        "source_ref": "main",
                    },
                }
            ),
            encoding="utf-8",
        )
        invalid_event.write_text(
            json.dumps(
                {
                    "action": "action-face-canary",
                    "client_payload": {
                        "job_id": "canary-20260716-002",
                        "source_ref": "main",
                        "unexpected": "blocked",
                    },
                }
            ),
            encoding="utf-8",
        )

        planner_env = {**os.environ, "GITHUB_OUTPUT": str(planner_output)}
        for secret_name in SENSITIVE_ENV:
            planner_env.pop(secret_name, None)
        valid = subprocess.run(
            [
                sys.executable,
                "scripts/action_face_plan.py",
                "--event",
                str(valid_event),
            ],
            cwd=workspace,
            env=planner_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
            shell=False,
        )
        invalid = subprocess.run(
            [
                sys.executable,
                "scripts/action_face_plan.py",
                "--event",
                str(invalid_event),
            ],
            cwd=workspace,
            env=planner_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
            shell=False,
        )
        record(
            "planner-positive-negative",
            valid.returncode == 0 and invalid.returncode != 0,
            f"valid={valid.returncode}; invalid={invalid.returncode}",
        )

        specialized_results: list[str] = []
        specialized_ok = True
        for pillar, action, job_id in SPECIALIZED_ACTIONS:
            specialized_output = temporary_path / f"{pillar}-specialized-output.txt"
            specialized_env = {
                **planner_env,
                "GITHUB_OUTPUT": str(specialized_output),
            }
            command = [
                sys.executable,
                "scripts/action_face_plan.py",
                "--event",
                str(manual_event),
                "--pillar",
                pillar,
                "--action",
                action,
                "--job-id",
                job_id,
            ]
            accepted = subprocess.run(
                [*command, "--source-ref", CANARY_SOURCE_SHA],
                cwd=workspace,
                env=specialized_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
                shell=False,
            )
            mutable = subprocess.run(
                [*command, "--source-ref", "main"],
                cwd=workspace,
                env=specialized_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
                shell=False,
            )
            specialized_results.append(
                f"{action}:accepted={accepted.returncode},mutable={mutable.returncode}"
            )
            if accepted.returncode != 0 or mutable.returncode == 0:
                specialized_ok = False
        record(
            "specialized-planner-positive-negative",
            specialized_ok,
            "; ".join(specialized_results),
        )

        issue_event = temporary_path / "issue.json"
        issue_event.write_text(
            json.dumps(
                {
                    "issue": {
                        "user": {"login": "GlacierEQ", "id": 194243768},
                        "author_association": "OWNER",
                    }
                }
            ),
            encoding="utf-8",
        )
        auth_env = {
            **os.environ,
            "GITHUB_REPOSITORY": "GlacierEQ/public-actions-runner-host",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_ACTOR": "GlacierEQ",
            "GITHUB_ACTOR_ID": "194243768",
            "GITHUB_EVENT_PATH": str(issue_event),
            "GITHUB_OUTPUT": str(auth_output),
        }
        for secret_name in SENSITIVE_ENV:
            auth_env.pop(secret_name, None)
        authorized = subprocess.run(
            [sys.executable, "scripts/action_face_authorize.py"],
            cwd=workspace,
            env=auth_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
            shell=False,
        )
        issue_event.write_text(
            json.dumps(
                {
                    "issue": {
                        "user": {"login": "intruder", "id": 999},
                        "author_association": "NONE",
                    }
                }
            ),
            encoding="utf-8",
        )
        unauthorized = subprocess.run(
            [sys.executable, "scripts/action_face_authorize.py"],
            cwd=workspace,
            env={
                **auth_env,
                "GITHUB_ACTOR": "intruder",
                "GITHUB_ACTOR_ID": "999",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
            shell=False,
        )
        record(
            "authorization-positive-negative",
            authorized.returncode == 0 and unauthorized.returncode != 0,
            f"authorized={authorized.returncode}; unauthorized={unauthorized.returncode}",
        )
        record(
            "subprocess-output-isolation",
            planner_output.exists() and auth_output.exists(),
            "canary subprocesses wrote only to temporary output files",
        )

    failed = [check for check in checks if check["status"] != "pass"]
    digest = hashlib.sha256(
        json.dumps(checks, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return catalog.write_result(
        plan,
        result_path,
        "failed" if failed else "completed",
        checks=checks,
        check_count=len(checks),
        failed_count=len(failed),
        checks_sha256=digest,
    )
