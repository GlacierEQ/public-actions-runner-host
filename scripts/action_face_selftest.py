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
SENSITIVE_ENV = {"APEX_CONTROL_TOKEN", "APEX_PRIVATE_READ_TOKEN", "GH_PAT"}


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail[:500]})

    leaked = sorted(key for key in SENSITIVE_ENV if os.environ.get(key))
    record("workload-secret-isolation", not leaked, "no protected token names are populated" if not leaked else f"unexpected variables: {', '.join(leaked)}")

    script_files = sorted((workspace / "scripts").glob("*.py"))
    syntax_failures: list[str] = []
    for path in script_files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            syntax_failures.append(f"{path.name}:{exc.lineno}")
    record("python-syntax", bool(script_files) and not syntax_failures, ", ".join(syntax_failures) or f"{len(script_files)} scripts compiled")

    json_failures: list[str] = []
    json_files = sorted((workspace / "config").glob("*.json"))
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            json_failures.append(f"{path.name}:{type(exc).__name__}")
    record("json-contracts", bool(json_files) and not json_failures, ", ".join(json_failures) or f"{len(json_files)} JSON files parsed")

    workflow_path = workspace / ".github" / "workflows" / "apex-pillar-runner.yml"
    workflow = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    required_fragments = [
        "name: APEX Public Action Face",
        "runs-on: ubuntu-latest",
        "scripts/action_face_authorize.py",
        "scripts/action_face_guard.py",
        "scripts/action_face_control_plane_guard.py",
        CHECKOUT_PIN,
    ]
    forbidden_fragments = [
        "runs-on: self-hosted",
        "secrets.GH_PAT",
        "actions/github-script@",
        "actions/checkout@v",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in workflow]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in workflow]
    record("workflow-invariants", bool(workflow) and not missing and not forbidden, f"missing={missing}; forbidden={forbidden}")

    catalog_entries: list[dict] = []
    for name in ("pillar-actions.json", "action-face-actions.json"):
        path = workspace / "config" / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            catalog_entries.extend(data.get("actions", []))
    keys = [(item.get("pillar"), item.get("action")) for item in catalog_entries]
    targets = [str(item.get("target_repo", "")) for item in catalog_entries]
    catalog_ok = bool(keys) and len(keys) == len(set(keys)) and all(target.startswith("GlacierEQ/") for target in targets)
    record("catalog-uniqueness", catalog_ok, f"{len(keys)} catalog actions checked")

    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        planner_output = temp_path / "planner-output.txt"
        auth_output = temp_path / "auth-output.txt"

        valid_event = temp_path / "valid.json"
        valid_event.write_text(json.dumps({"action": "action-face-canary", "client_payload": {"job_id": "canary-20260716-001", "source_ref": "main"}}), encoding="utf-8")
        invalid_event = temp_path / "invalid.json"
        invalid_event.write_text(json.dumps({"action": "action-face-canary", "client_payload": {"job_id": "canary-20260716-002", "source_ref": "main", "unexpected": "blocked"}}), encoding="utf-8")

        planner_env = {**os.environ, "GITHUB_OUTPUT": str(planner_output)}
        for secret_name in SENSITIVE_ENV:
            planner_env.pop(secret_name, None)
        valid = subprocess.run(
            [sys.executable, "scripts/action_face_plan.py", "--event", str(valid_event)],
            cwd=workspace,
            env=planner_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        invalid = subprocess.run(
            [sys.executable, "scripts/action_face_plan.py", "--event", str(invalid_event)],
            cwd=workspace,
            env=planner_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        record("planner-positive-negative", valid.returncode == 0 and invalid.returncode != 0, f"valid={valid.returncode}; invalid={invalid.returncode}")

        issue_event = temp_path / "issue.json"
        issue_event.write_text(json.dumps({"issue": {"user": {"login": "GlacierEQ"}, "author_association": "OWNER"}}), encoding="utf-8")
        auth_env = {
            **os.environ,
            "GITHUB_REPOSITORY": "GlacierEQ/public-actions-runner-host",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_ACTOR": "GlacierEQ",
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
        )
        issue_event.write_text(json.dumps({"issue": {"user": {"login": "intruder"}, "author_association": "NONE"}}), encoding="utf-8")
        unauthorized = subprocess.run(
            [sys.executable, "scripts/action_face_authorize.py"],
            cwd=workspace,
            env={**auth_env, "GITHUB_ACTOR": "intruder"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        record("authorization-positive-negative", authorized.returncode == 0 and unauthorized.returncode != 0, f"authorized={authorized.returncode}; unauthorized={unauthorized.returncode}")

        outer_output_untouched = os.environ.get("GITHUB_OUTPUT", "") not in {str(planner_output), str(auth_output)}
        record("subprocess-output-isolation", outer_output_untouched, "canary subprocesses use temporary output files")

    failed = [check for check in checks if check["status"] != "pass"]
    digest = hashlib.sha256(json.dumps(checks, sort_keys=True).encode("utf-8")).hexdigest()
    return catalog.write_result(
        plan,
        result_path,
        "failed" if failed else "completed",
        checks=checks,
        check_count=len(checks),
        failed_count=len(failed),
        checks_sha256=digest,
    )
