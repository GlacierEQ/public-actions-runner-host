"""Bounded Code-domain validator for the computer-user Tool System."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import apex_catalog_runner as catalog

EXPECTED_ACTION = "code.tool-system.validate"
EXPECTED_ADAPTER = "tool-system-validate"
EXPECTED_REPOSITORY = "GlacierEQ/computer-user"
SENSITIVE_ENV = {
    "APEX_BRANCH_WRITE_TOKEN",
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "GH_PAT",
    "GITHUB_TOKEN",
}
REQUIRED_PATHS = (
    "config/tool_system.json",
    "runtime/tool_catalog.py",
    "runtime/smithery_gateway.py",
    "runtime/tool_system.py",
    "scripts/ci/verify_tool_system.py",
    "tests/test_tool_system.py",
)
LINT_PATHS = (
    "runtime/tool_catalog.py",
    "runtime/smithery_gateway.py",
    "runtime/tool_system.py",
    "scripts/ci/verify_tool_system.py",
    "tests/test_tool_system.py",
)


def isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in SENSITIVE_ENV:
        env.pop(key, None)
    env.update(
        {
            "CI": "true",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    return env


def validate_plan(plan: dict) -> str | None:
    if plan.get("action") != EXPECTED_ACTION:
        return "action identity mismatch"
    if plan.get("adapter") != EXPECTED_ADAPTER:
        return "adapter identity mismatch"
    if plan.get("source_repo") != EXPECTED_REPOSITORY:
        return "source repository identity mismatch"
    if plan.get("target_repo") != EXPECTED_REPOSITORY:
        return "target repository identity mismatch"
    return None


def command_sequence(result_path: Path, job_id: str) -> list[list[str]]:
    venv = result_path.resolve().parent / f"venv-{job_id}"
    python = venv / "bin" / "python"
    return [
        [sys.executable, "-m", "venv", str(venv)],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "pytest==9.1.1",
            "ruff==0.16.1",
        ],
        [
            str(python),
            "-m",
            "compileall",
            "-q",
            "runtime",
            "tests",
            "scripts/ci/verify_tool_system.py",
        ],
        [str(python), "scripts/ci/verify_tool_system.py"],
        [str(python), "-m", "pytest", "-q", "tests/test_tool_system.py"],
        [str(python), "-m", "ruff", "check", *LINT_PATHS],
    ]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    error = validate_plan(plan)
    if error:
        return catalog.write_result(plan, result_path, "blocked", reason=error)

    missing = [path for path in REQUIRED_PATHS if not (workspace / path).is_file()]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required Tool System files are missing: " + ", ".join(missing),
        )

    steps: list[dict] = []
    env = isolated_env()
    failed = False
    for command in command_sequence(result_path, str(plan["job_id"])):
        executable = Path(command[0])
        if not executable.is_absolute() and shutil.which(command[0]) is None:
            steps.append(
                {
                    "command": command,
                    "status": "blocked",
                    "reason": f"{command[0]} is unavailable",
                }
            )
            failed = True
            break
        try:
            process = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=1800,
                check=False,
                env=env,
            )
            output = process.stdout[-100_000:]
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
                failed = True
                break
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": "timeout after 1800 seconds",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            failed = True
            break
        except OSError as exc:
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": f"process start failed: {type(exc).__name__}: {exc}",
                }
            )
            failed = True
            break

    return catalog.write_result(
        plan,
        result_path,
        "failed" if failed else "completed",
        steps=steps,
        command_contract_sha256=hashlib.sha256(
            repr(command_sequence(result_path, str(plan["job_id"]))).encode()
        ).hexdigest(),
    )
