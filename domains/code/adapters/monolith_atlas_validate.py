"""Run the fixed Monolith atlas gates that cannot allocate private runners."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import apex_catalog_runner as catalog

EXPECTED_ACTION = "code.monolith.validate-atlases"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "test"
SHA = re.compile(r"^[0-9a-f]{40}$")
SENSITIVE_ENV = {
    "APEX_BRANCH_WRITE_TOKEN",
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "GH_PAT",
    "GITHUB_TOKEN",
}
REQUIRED_PATHS = (
    "scripts/validate_function_atlas.py",
    "tests/test_function_atlas.py",
    "scripts/build_monolith_command_atlas.py",
    "scripts/query_monolith.py",
    "tests/test_monolith_command_atlas.py",
    "tests/test_query_monolith.py",
    "catalog/library.json",
    "catalog/monolith_command_atlas.json",
    "status/MONOLITH_COMMAND_ATLAS.md",
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


def isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in SENSITIVE_ENV:
        env.pop(key, None)
    env.update(
        {
            "CI": "true",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def commands(result_path: Path, job_id: str) -> list[list[str]]:
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
            "pytest==8.4.1",
        ],
        [
            str(python),
            "-m",
            "py_compile",
            "scripts/validate_function_atlas.py",
            "tests/test_function_atlas.py",
        ],
        [str(python), "scripts/validate_function_atlas.py"],
        [str(python), "-m", "unittest", "tests.test_function_atlas"],
        [str(python), "scripts/build_monolith_command_atlas.py", "--check"],
        [str(python), "scripts/query_monolith.py", "summary", "--format", "json"],
        [
            str(python),
            "scripts/query_monolith.py",
            "repos",
            "--has-evidence",
            "--format",
            "json",
            "--limit",
            "0",
        ],
        [
            str(python),
            "scripts/query_monolith.py",
            "actions",
            "--priority",
            "P0",
            "--format",
            "json",
        ],
        [str(python), "scripts/query_monolith.py", "domains", "--format", "json"],
        [
            str(python),
            "-m",
            "pytest",
            "-q",
            "tests/test_monolith_command_atlas.py",
            "tests/test_query_monolith.py",
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

    missing = [path for path in REQUIRED_PATHS if not (workspace / path).is_file()]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required Monolith atlas files are missing: " + ", ".join(missing),
        )

    sequence = commands(result_path, str(plan["job_id"]))
    steps: list[dict] = []
    status = "completed"
    env = isolated_env()
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

    return catalog.write_result(
        plan,
        result_path,
        status,
        steps=steps,
        command_contract_sha256=hashlib.sha256(repr(sequence).encode()).hexdigest(),
        validated_gates=["core-function-atlas", "monolith-command-atlas"],
    )
