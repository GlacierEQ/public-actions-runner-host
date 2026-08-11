"""Bounded Code-domain validator for the computer-user Smithery tool system."""

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

LEGACY_REQUIRED_PATHS = (
    "config/tool_system.json",
    "runtime/tool_catalog.py",
    "runtime/smithery_gateway.py",
    "runtime/tool_system.py",
    "scripts/ci/verify_tool_system.py",
    "tests/test_tool_system.py",
)
LEGACY_LINT_PATHS = (
    "runtime/tool_catalog.py",
    "runtime/smithery_gateway.py",
    "runtime/tool_system.py",
    "scripts/ci/verify_tool_system.py",
    "tests/test_tool_system.py",
)
# Backward-compatible public adapter contract used by existing runner regressions.
REQUIRED_PATHS = LEGACY_REQUIRED_PATHS
LINT_PATHS = LEGACY_LINT_PATHS

BOUNDED_REQUIRED_PATHS = (
    "runtime/governed_runtime.py",
    "runtime/tool_policy.py",
    "tooltruck/harvest/bounded_discovery.py",
    "tooltruck/harvest/crawl_ledger.py",
    "tooltruck/harvest/crawl_projector.py",
    "tooltruck/harvest/crawl_runner.py",
    "tooltruck/harvest/observation_chunks.py",
    "tooltruck/harvest/source_adapters.py",
    "tooltruck/harvest/source_registry.py",
    "tooltruck/harvest/seed_crawl.py",
    "tests/test_tool_policy.py",
    "tests/test_tooltruck_bounded_discovery.py",
    "tests/test_tooltruck_crawl_projector.py",
    "tests/test_tooltruck_schema_contracts.py",
    "tooltruck/tests/test_bounded_discovery.py",
    "tooltruck/tests/test_crawl_ledger.py",
    "tooltruck/tests/test_crawl_projector.py",
    "tooltruck/tests/test_crawl_runner.py",
    "tooltruck/tests/test_observation_chunks.py",
    "tooltruck/tests/test_seed_crawl.py",
    "tooltruck/tests/test_smithery_tool_harvester.py",
    "tooltruck/tests/test_source_adapters.py",
    "tooltruck/tests/test_source_registry.py",
)
BOUNDED_TEST_PATHS = tuple(
    path
    for path in BOUNDED_REQUIRED_PATHS
    if path.startswith(("tests/", "tooltruck/tests/"))
)
BOUNDED_LINT_PATHS = tuple(
    path for path in BOUNDED_REQUIRED_PATHS if path.endswith(".py")
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


def _surface_state(workspace: Path, paths: tuple[str, ...]) -> tuple[str, list[str]]:
    present = [path for path in paths if (workspace / path).is_file()]
    missing = [path for path in paths if path not in present]
    if not present:
        return "absent", missing
    if missing:
        return "partial", missing
    return "complete", []


def _surface(workspace: Path) -> tuple[str, str | None]:
    legacy_state, legacy_missing = _surface_state(workspace, LEGACY_REQUIRED_PATHS)
    bounded_state, bounded_missing = _surface_state(workspace, BOUNDED_REQUIRED_PATHS)

    if legacy_state == "complete" and bounded_state == "complete":
        return "bounded-smithery-v7", None
    if bounded_state == "complete" and legacy_state == "absent":
        return "bounded-smithery-v7", None
    if legacy_state == "complete" and bounded_state == "absent":
        return "tool-system-v2", None
    if legacy_state == "partial":
        return "blocked", "partial legacy Tool System surface; missing: " + ", ".join(
            legacy_missing
        )
    if bounded_state == "partial":
        return "blocked", "partial bounded Smithery surface; missing: " + ", ".join(
            bounded_missing
        )
    return (
        "blocked",
        "required Tool System files are missing: " + ", ".join(LEGACY_REQUIRED_PATHS),
    )


def command_sequence(
    result_path: Path, job_id: str, surface: str = "tool-system-v2"
) -> list[list[str]]:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("job_id is invalid")
    venv = result_path.resolve().parent / f"venv-{job_id}"
    python = venv / "bin" / "python"
    prefix = [
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
    ]
    if surface == "tool-system-v2":
        return prefix + [
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
            [str(python), "-m", "ruff", "check", *LEGACY_LINT_PATHS],
        ]
    if surface == "bounded-smithery-v7":
        return prefix + [
            [
                str(python),
                "-m",
                "compileall",
                "-q",
                "runtime",
                "tooltruck/harvest",
                "tests",
                "tooltruck/tests",
            ],
            [str(python), "-m", "pytest", "-q", *BOUNDED_TEST_PATHS],
            [str(python), "-m", "ruff", "check", *BOUNDED_LINT_PATHS],
        ]
    raise ValueError("unsupported Tool System surface")


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

    surface, surface_error = _surface(workspace)
    if surface == "blocked":
        return write_blocked(
            normalized, result_path, surface_error or "Tool System surface is invalid"
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

    commands = command_sequence(result_path, normalized["job_id"], surface)
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
