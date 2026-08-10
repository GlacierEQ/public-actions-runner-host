"""Validate promoted FILEBOSS security repairs in active successor repositories."""

from __future__ import annotations

import hashlib
import os
import re
import stat
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

EXPECTED_ADAPTER = "fileboss_security_validate"
SHA = re.compile(r"^[0-9a-f]{40}$")

ACTION_SPECS: dict[str, dict[str, object]] = {
    "code.scribe.validate-fileboss-security": {
        "repository": "GlacierEQ/scribe-multimodal-master",
        "required_paths": (
            "engines/fileboss-whisperx-processor/backend/api/ide_endpoints.py",
            "engines/fileboss-whisperx-processor/backend/app/routers/upload.py",
            "engines/fileboss-whisperx-processor/backend/tests/test_ide_git_path_boundaries.py",
            "engines/fileboss-whisperx-processor/backend/tests/test_upload_stream_bounds.py",
        ),
        "commands": (
            (
                sys.executable,
                "-m",
                "py_compile",
                "engines/fileboss-whisperx-processor/backend/api/ide_endpoints.py",
                "engines/fileboss-whisperx-processor/backend/app/routers/upload.py",
                "engines/fileboss-whisperx-processor/backend/tests/test_ide_git_path_boundaries.py",
                "engines/fileboss-whisperx-processor/backend/tests/test_upload_stream_bounds.py",
            ),
            (
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "engines/fileboss-whisperx-processor/backend/tests",
                "-p",
                "test_ide_git_path_boundaries.py",
            ),
            (
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "engines/fileboss-whisperx-processor/backend/tests",
                "-p",
                "test_upload_stream_bounds.py",
            ),
        ),
        "validated_gates": (
            "fileboss-ide-python-syntax",
            "fileboss-ide-path-boundaries",
            "fileboss-upload-stream-bounds",
        ),
    },
    "code.sigma.validate-fileboss-security": {
        "repository": "GlacierEQ/sigma-file-manager",
        "required_paths": (
            "integrations/fileboss/backend/ide_backend.py",
            "integrations/fileboss/tests/test_ide_git_path_boundaries.py",
        ),
        "commands": (
            (
                sys.executable,
                "-m",
                "py_compile",
                "integrations/fileboss/backend/ide_backend.py",
                "integrations/fileboss/tests/test_ide_git_path_boundaries.py",
            ),
            (
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "integrations/fileboss/tests",
                "-p",
                "test_ide_git_path_boundaries.py",
            ),
        ),
        "validated_gates": (
            "fileboss-ide-python-syntax",
            "fileboss-ide-path-boundaries",
        ),
    },
}


def validate_plan(plan: dict) -> dict[str, object]:
    action = plan.get("action")
    spec = ACTION_SPECS.get(str(action))
    if spec is None:
        raise ValueError("action identity mismatch")
    repository = str(spec["repository"])
    expected = {
        "pillar": "C",
        "adapter": EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": repository,
        "target_repo": repository,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise ValueError(f"{field} identity mismatch")
    return spec


def inspect_required_paths(
    workspace: Path, required_paths: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """Return missing and unsafe required paths without following symlinks."""
    missing: list[str] = []
    unsafe: list[str] = []
    for relative in required_paths:
        candidate = workspace / relative
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            missing.append(relative)
            continue
        except OSError:
            unsafe.append(relative)
            continue

        if not stat.S_ISREG(metadata.st_mode):
            unsafe.append(relative)
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            unsafe.append(relative)
            continue
        if not resolved.is_relative_to(workspace):
            unsafe.append(relative)
    return missing, unsafe


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    try:
        spec = validate_plan(plan)
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

    required_paths = tuple(str(item) for item in spec["required_paths"])
    missing, unsafe = inspect_required_paths(workspace, required_paths)
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required security verification files are missing: " + ", ".join(missing),
        )
    if unsafe:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=(
                "required security verification paths are not regular contained files: "
                + ", ".join(unsafe)
            ),
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

    sequence = [list(command) for command in spec["commands"]]
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
        validated_gates=list(spec["validated_gates"]),
        workspace_attestation={"before": pre_attestation, "after": post_attestation},
    )
