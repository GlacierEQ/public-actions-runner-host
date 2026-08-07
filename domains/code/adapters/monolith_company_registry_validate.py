"""Validate the private Monolith company-engineered registry."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
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

EXPECTED_ACTION = "code.monolith.validate-company-engineered-registry"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "monolith_company_registry_validate"
SHA = re.compile(r"^[0-9a-f]{40}$")
PYTEST_MAJOR_MIN = 8
PYTEST_MAJOR_MAX_EXCLUSIVE = 9
REQUIRED_PATHS = (
    "catalog/company_engineered_repositories.json",
    "domains/company_engineered_portfolio.md",
    "status/COMPANY_ENGINEERED_PROCESSING.md",
    "docs/CAREER_POSITIONING_TRUTH_BOUNDARY.md",
    "tests/test_company_engineered_registry.py",
    "automation/public-runner/company-engineered-registry.request.json",
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
        [sys.executable, "-m", "json.tool", "catalog/company_engineered_repositories.json"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_company_engineered_registry.py",
        ],
    ]


def pytest_runtime() -> tuple[bool, str | None, str | None]:
    if importlib.util.find_spec("pytest") is None:
        return False, None, "governed pytest runtime is unavailable"
    try:
        version = importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        return False, None, "governed pytest runtime metadata is unavailable"
    match = re.match(r"^(\d+)(?:\.|$)", version)
    major = int(match.group(1)) if match else None
    if major is None or not (PYTEST_MAJOR_MIN <= major < PYTEST_MAJOR_MAX_EXCLUSIVE):
        return (
            False,
            version,
            f"unsupported pytest runtime {version}; required major version is 8",
        )
    return True, version, None


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

    available, pytest_version, runtime_error = pytest_runtime()
    if not available:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=runtime_error,
            runtime={"pytest": pytest_version},
        )

    missing = [relative for relative in REQUIRED_PATHS if not (workspace / relative).is_file()]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required company-registry files are missing: " + ", ".join(missing),
            runtime={"pytest": pytest_version},
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
            runtime={"pytest": pytest_version},
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
                timeout=1800,
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
                    "reason": "timeout after 1800 seconds",
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
        validated_gates=["company-registry-json", "company-registry-truth-surfaces"],
        runtime={"pytest": pytest_version},
        workspace_attestation={"before": pre_attestation, "after": post_attestation},
    )
