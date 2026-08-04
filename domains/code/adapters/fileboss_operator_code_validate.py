"""Validate the private FILEBOSS Operator Code control bridge."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import apex_catalog_runner as catalog
from scripts.workload_isolation import (
    WorkloadIsolationError,
    attest_workspace,
    build_environment,
    command_contract_sha256,
)

EXPECTED_ACTION = "code.fileboss.validate-operator-code-bridge"
EXPECTED_REPOSITORY = "GlacierEQ/FILEBOSS"
EXPECTED_ADAPTER = "fileboss_operator_code_validate"
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PATHS = (
    "genius/shared/integrations/operator_code_gateway.py",
    "scripts/operator_code_bridge.py",
    "tests/test_operator_code_gateway.py",
    "genius/contracts/operator-code-job.schema.json",
    "genius/docs/OPERATOR_CODE_CONTROL_BRIDGE.md",
    "automation/public-runner/operator-code-bridge.request.json",
)


def validate_plan(plan: dict) -> None:
    expected = {
        "pillar": "C",
        "action": EXPECTED_ACTION,
        "adapter": EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": EXPECTED_REPOSITORY,
        "target_repo": EXPECTED_REPOSITORY,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise ValueError(f"{field} identity mismatch")


def commands() -> list[list[str]]:
    return [
        [
            sys.executable,
            "-m",
            "json.tool",
            "genius/contracts/operator-code-job.schema.json",
        ],
        [
            sys.executable,
            "-m",
            "py_compile",
            "genius/shared/integrations/operator_code_gateway.py",
            "scripts/operator_code_bridge.py",
            "tests/test_operator_code_gateway.py",
        ],
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_operator_code_gateway.py",
        ],
    ]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    try:
        validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA is unavailable or invalid",
        )
    if resolved_sha != plan.get("source_ref"):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA does not match requested source_ref",
        )

    missing = [relative for relative in REQUIRED_PATHS if not (workspace / relative).is_file()]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required Operator Code bridge files are missing: " + ", ".join(missing),
        )

    try:
        pre_attestation = attest_workspace(workspace, resolved_sha)
        env = build_environment(result_path, str(plan["job_id"]))
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"workload isolation failed before execution: {error}",
        )

    sequence = commands()
    steps: list[dict] = []
    status = "completed"
    for command in sequence:
        try:
            process = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=False,
                shell=False,
                env=env,
            )
            output = (process.stdout or "")[-100_000:]
            steps.append(
                {
                    "command": command,
                    "exit_code": process.returncode,
                    "status": "completed" if process.returncode == 0 else "failed",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            if process.returncode != 0:
                status = "failed"
                break
        except subprocess.TimeoutExpired as error:
            output = error.stdout if isinstance(error.stdout, str) else ""
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": "timeout after 900 seconds",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            status = "failed"
            break
        except OSError as error:
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": f"process start failed: {type(error).__name__}: {error}",
                }
            )
            status = "failed"
            break

    post_attestation: dict[str, object] | None = None
    try:
        post_attestation = attest_workspace(workspace, resolved_sha)
    except WorkloadIsolationError as error:
        status = "failed"
        steps.append(
            {
                "command": ["workload-attestation"],
                "status": "failed",
                "reason": str(error),
            }
        )

    return catalog.write_result(
        plan,
        result_path,
        status,
        steps=steps,
        command_contract_sha256=command_contract_sha256(sequence),
        validated_gates=[
            "operator-code-job-schema",
            "operator-code-python-syntax",
            "operator-code-security-tests",
        ],
        workspace_attestation={"before": pre_attestation, "after": post_attestation},
    )
