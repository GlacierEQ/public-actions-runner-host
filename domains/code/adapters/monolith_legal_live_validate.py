"""Validate Monolith legal-live reconciliation at an exact private source SHA."""

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
    attest_checkout,
    build_environment,
    command_contract_sha256,
    open_checkout,
)

EXPECTED_ACTION = "code.monolith.validate-legal-live-reconciliation"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "test"
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PATHS = (
    "scripts/validate_legal_live_reconciliation.py",
    "tests/test_legal_live_reconciliation.py",
    "scripts/validate_legal_case.py",
    "tests/test_legal_case.py",
    "tests/test_legal_spine.py",
    "tests/test_sync_legal_spines.py",
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
        [sys.executable, "-m", "py_compile", "scripts/validate_legal_live_reconciliation.py", "tests/test_legal_live_reconciliation.py"],
        [sys.executable, "scripts/validate_legal_live_reconciliation.py"],
        [sys.executable, "-m", "unittest", "tests.test_legal_live_reconciliation"],
        [sys.executable, "scripts/validate_legal_case.py"],
        [sys.executable, "-m", "unittest", "tests.test_legal_case", "tests.test_legal_spine", "tests.test_sync_legal_spines"],
    ]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    result_path = result_path.resolve()
    try:
        validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return catalog.write_result(plan, result_path, "blocked", reason="resolved source SHA is unavailable or invalid")

    try:
        checkout = open_checkout(workspace, label="workload")
        env = build_environment(result_path, str(plan["job_id"]))
    except WorkloadIsolationError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=f"workload isolation failed before execution: {error}")

    sequence = commands()
    steps: list[dict] = []
    status = "completed"
    post_attestation: dict[str, object] | None = None

    with checkout:
        try:
            pre_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            return catalog.write_result(plan, result_path, "blocked", reason=f"workload isolation failed before execution: {error}")

        root = checkout.proc_path
        missing = [path for path in REQUIRED_PATHS if not (root / path).is_file()]
        if missing:
            return catalog.write_result(plan, result_path, "blocked", reason="required Monolith legal-live files are missing: " + ", ".join(missing))

        for command in sequence:
            try:
                process = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800, check=False, shell=False, env=env, pass_fds=checkout.pass_fds)
                output = (process.stdout or "")[-100_000:]
                steps.append({"command": command, "exit_code": process.returncode, "status": "completed" if process.returncode == 0 else "failed", "output_sha256": hashlib.sha256(output.encode()).hexdigest(), "output_tail": output[-24_000:]})
                if process.returncode != 0:
                    status = "failed"
                    break
            except (subprocess.TimeoutExpired, OSError) as error:
                steps.append({"command": command, "status": "failed", "reason": f"{type(error).__name__}: {error}"})
                status = "failed"
                break

        try:
            post_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            status = "failed"
            steps.append({"command": ["workload-attestation"], "status": "failed", "reason": str(error)})

    return catalog.write_result(
        plan,
        result_path,
        status,
        steps=steps,
        command_contract_sha256=command_contract_sha256(sequence, volatile_roots=(result_path.parent,)),
        validated_gates=["legal-live-reconciliation", "legal-case", "legal-spine-sync"],
        workspace_attestation={"before": pre_attestation, "after": post_attestation},
    )
