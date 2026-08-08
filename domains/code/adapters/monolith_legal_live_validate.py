"""Validate the private Monolith legal live-reconciliation overlay."""

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

EXPECTED_ACTION = "code.monolith.validate-legal-live-reconciliation"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "monolith_legal_live_validate"
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PATHS = (
    "catalog/legal_source_inspection_registry.json",
    "status/LEGAL_LIVE_RECONCILIATION.md",
    "status/legal_live/LIVE_SOURCE_INSPECTION_EXPANSION.md",
    "docs/legal/LEGAL_MONOLITH_INTEGRATION_CONTRACT.md",
    "scripts/validate_legal_live_reconciliation.py",
    "tests/test_legal_live_reconciliation.py",
    "automation/public-runner/legal-live-reconciliation.request.json",
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
            "py_compile",
            "scripts/validate_legal_live_reconciliation.py",
            "tests/test_legal_live_reconciliation.py",
        ],
        [sys.executable, "scripts/validate_legal_live_reconciliation.py"],
        [sys.executable, "-m", "unittest", "tests.test_legal_live_reconciliation"],
    ]


def bind_attested_python_root(env: dict[str, str], workspace: Path) -> dict[str, str]:
    """Expose only the exact attested workload root when safe-path isolation is active."""

    if env.get("PYTHONSAFEPATH") == "1":
        env["PYTHONPATH"] = str(workspace)
    return env


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

    missing = [
        relative for relative in REQUIRED_PATHS if not (workspace / relative).is_file()
    ]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required legal reconciliation files are missing: "
            + ", ".join(missing),
        )

    try:
        pre_attestation = attest_workspace(workspace, resolved_sha)
        env = bind_attested_python_root(
            build_environment(result_path, str(plan["job_id"])),
            workspace,
        )
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
            "legal-live-registry",
            "legal-live-board",
            "integration-contract",
        ],
        workspace_attestation={"before": pre_attestation, "after": post_attestation},
    )
