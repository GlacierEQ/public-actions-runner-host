"""Bounded Code-domain validator for the computer-user Tool System."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import apex_catalog_runner as catalog

EXPECTED_DOMAIN = "code"
EXPECTED_ACTION = "code.tool-system.validate"
EXPECTED_ADAPTER = "tool-system-validate"
EXPECTED_REPOSITORY = "GlacierEQ/computer-user"
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
SOURCE_REF = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]+$")
CANONICAL_JOB_KEYS = frozenset(
    {
        "job_id",
        "domain",
        "action",
        "source_ref",
        "expected_source_sha",
        "approval_id",
    }
)
LEGACY_PLAN_KEYS = frozenset(
    {
        "job_id",
        "pillar",
        "action",
        "adapter",
        "task",
        "source_repo",
        "source_ref",
        "target_repo",
        "approval_id",
        "approval_required",
        "expected_source_sha",
        "workflow_run_id",
        "workflow_run_attempt",
        "trigger_actor",
        "trigger_actor_id",
        "event_name",
        "execution_repo",
        "public_runner_sha",
    }
)
SAFE_ENV_KEYS = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TMP",
        "TMPDIR",
    }
)
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


def isolated_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    host = source or os.environ
    env = {key: value for key, value in host.items() if key in SAFE_ENV_KEYS}
    env.update(
        {
            "CI": "true",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def _valid_source_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and bool(SOURCE_REF.fullmatch(value))
    )


def _base_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {
        "job_id": str(plan.get("job_id") or "invalid-tool-system-plan"),
        "pillar": "C",
        "action": EXPECTED_ACTION,
        "adapter": EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": EXPECTED_REPOSITORY,
        "source_ref": str(plan.get("source_ref") or ""),
        "target_repo": EXPECTED_REPOSITORY,
        "approval_id": str(plan.get("approval_id") or ""),
        "approval_required": "false",
        "expected_source_sha": plan.get("expected_source_sha") or None,
    }
    for key in (
        "workflow_run_id",
        "workflow_run_attempt",
        "trigger_actor",
        "trigger_actor_id",
        "event_name",
        "execution_repo",
        "public_runner_sha",
    ):
        normalized[key] = str(plan.get(key) or "")
    return normalized


def blocked_plan(plan: object) -> dict[str, Any]:
    source = plan if isinstance(plan, Mapping) else {}
    blocked = _base_plan(source)
    if not JOB_ID.fullmatch(blocked["job_id"]):
        blocked["job_id"] = "invalid-tool-system-plan"
    if not _valid_source_ref(blocked["source_ref"]):
        blocked["source_ref"] = ""
    expected = blocked.get("expected_source_sha")
    if expected is not None and not (
        isinstance(expected, str) and SHA.fullmatch(expected)
    ):
        blocked["expected_source_sha"] = None
    return blocked


def normalize_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise TypeError("plan must be an object")

    is_canonical = "domain" in plan
    allowed = CANONICAL_JOB_KEYS if is_canonical else LEGACY_PLAN_KEYS
    unknown = sorted(set(plan) - allowed)
    if unknown:
        raise ValueError("plan contains unsupported fields: " + ", ".join(unknown))

    job_id = plan.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("job_id is invalid")
    if not _valid_source_ref(plan.get("source_ref")):
        raise ValueError("source_ref is invalid")
    if plan.get("action") != EXPECTED_ACTION:
        raise ValueError("action identity mismatch")

    expected = plan.get("expected_source_sha") or None
    if expected is not None and not (
        isinstance(expected, str) and SHA.fullmatch(expected)
    ):
        raise ValueError("expected_source_sha is invalid")

    if is_canonical:
        if plan.get("domain") != EXPECTED_DOMAIN:
            raise ValueError("canonical plan domain must be code")
    else:
        identities = {
            "pillar": "C",
            "adapter": EXPECTED_ADAPTER,
            "task": "test",
            "source_repo": EXPECTED_REPOSITORY,
            "target_repo": EXPECTED_REPOSITORY,
        }
        for field, expected_value in identities.items():
            if plan.get(field) != expected_value:
                raise ValueError(f"{field.replace('_', ' ')} identity mismatch")

    return _base_plan(plan)


def command_sequence(result_path: Path, job_id: str) -> list[list[str]]:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("job_id is invalid")
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


def write_blocked(
    plan: object,
    result_path: Path,
    reason: str,
    *,
    expected_source_sha: str | None = None,
) -> int:
    normalized = blocked_plan(plan)
    expectation = expected_source_sha
    if expectation is None:
        value = normalized.get("expected_source_sha")
        expectation = value if isinstance(value, str) else None
    return catalog.write_result(
        normalized,
        result_path,
        "blocked",
        reason=reason,
        expected_source_sha=expectation,
    )


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    try:
        normalized = normalize_plan(plan)
    except (TypeError, ValueError) as error:
        return write_blocked(plan, result_path, str(error))

    missing = [path for path in REQUIRED_PATHS if not (workspace / path).is_file()]
    if missing:
        return write_blocked(
            normalized,
            result_path,
            "required Tool System files are missing: " + ", ".join(missing),
        )

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return write_blocked(
            normalized, result_path, "resolved source SHA is unavailable or invalid"
        )
    expected_sha = normalized.get("expected_source_sha")
    if expected_sha and expected_sha != resolved_sha:
        return write_blocked(
            normalized,
            result_path,
            "expected source SHA does not match resolved source SHA",
            expected_source_sha=expected_sha,
        )

    commands = command_sequence(result_path, normalized["job_id"])
    steps: list[dict] = []
    env = isolated_env()
    failed = False
    for command in commands:
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
            process = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
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
                failed = True
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
            failed = True
            break
        except OSError as error:
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": f"process start failed: {type(error).__name__}: {error}",
                }
            )
            failed = True
            break

    return catalog.write_result(
        normalized,
        result_path,
        "failed" if failed else "completed",
        steps=steps,
        expected_source_sha=expected_sha,
        command_contract_sha256=hashlib.sha256(repr(commands).encode()).hexdigest(),
    )
