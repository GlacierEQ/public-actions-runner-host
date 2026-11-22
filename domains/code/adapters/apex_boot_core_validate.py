#!/usr/bin/env python3
"""Exact-source validation adapter for GlacierEQ/apex-boot-core.

This public-runner adapter executes the private boot-core recovery branch without
writing to it. It binds execution to the resolved private source SHA and runs the
repo-owned APEX authority, runtime, receipt, projection, and regression checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ACTION_ID = "code.apex-boot-core.validate"
TARGET_REPOSITORY = "GlacierEQ/apex-boot-core"
JOB_SCHEMA = "glaciereq.action-face.code.apex-boot-core.validate.job.v1"
RESULT_SCHEMA = "glaciereq.action-face.code.apex-boot-core.validate.result.v1"
EXECUTION_LAW = "MAXIMUM_COHERENT_ADVANCE"
HUMAN_PROJECT_DIRECTION_AUTHORITY = "Casey Barton"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_root() -> Path:
    raw = os.environ.get("APEX_PRIVATE_REPO_ROOT", "").strip()
    if not raw:
        raise RuntimeError("APEX_PRIVATE_REPO_ROOT is required")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"private source root is not a directory: {root}")
    return root


def _resolved_sha() -> str:
    value = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise RuntimeError("APEX_RESOLVED_SOURCE_SHA must be a 40-character git SHA")
    return value


def _git_head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {proc.stderr.strip()}")
    return proc.stdout.strip().lower()


def _isolated_env(tmp_root: Path, source_sha: str) -> dict[str, str]:
    home = tmp_root / "home"
    temp = tmp_root / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    keep = {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    }
    env = {key: value for key, value in os.environ.items() if key in keep and value}
    env.update(
        {
            "HOME": str(home),
            "TMPDIR": str(temp),
            "TEMP": str(temp),
            "TMP": str(temp),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "APEX_RUNTIME_SOURCE_REVISION": source_sha,
        }
    )
    return env


def _run(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int = 180,
) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        env=dict(env),
        timeout=timeout,
    )
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    stdout = proc.stdout or b""
    stderr = proc.stderr or b""
    return {
        "name": name,
        "argv": list(argv),
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "duration_ms": duration_ms,
        "stdout_sha256": _sha256_bytes(stdout),
        "stderr_sha256": _sha256_bytes(stderr),
        "stdout_tail": stdout.decode("utf-8", errors="replace")[-4000:],
        "stderr_tail": stderr.decode("utf-8", errors="replace")[-4000:],
    }


def _validate_plan(plan: Mapping[str, Any]) -> tuple[str, str, str]:
    if plan.get("action") != ACTION_ID:
        raise ValueError(f"unexpected action: {plan.get('action')!r}")
    if plan.get("target_repository") != TARGET_REPOSITORY:
        raise ValueError(f"unexpected target_repository: {plan.get('target_repository')!r}")
    source_repository = str(plan.get("source_repository") or "").strip()
    if source_repository != TARGET_REPOSITORY:
        raise ValueError(f"unexpected source_repository: {source_repository!r}")
    job_id = str(plan.get("job_id") or "").strip()
    source_ref = str(plan.get("source_ref") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    if not source_ref:
        raise ValueError("source_ref is required")
    return job_id, source_repository, source_ref


def execute(
    *,
    plan: Mapping[str, Any],
    repo_root: Path | None = None,
    env_builder: Callable[[Path, str], Mapping[str, str]] = _isolated_env,
    runner: Callable[..., dict[str, Any]] = _run,
) -> dict[str, Any]:
    job_id, source_repository, source_ref = _validate_plan(plan)
    root = (repo_root or _source_root()).resolve()
    resolved_sha = _resolved_sha()
    actual_sha = _git_head(root)
    if actual_sha != resolved_sha:
        raise RuntimeError(
            f"private checkout SHA drift: expected resolved {resolved_sha}, got {actual_sha}"
        )
    if len(source_ref) == 40 and source_ref.lower() != resolved_sha:
        raise RuntimeError(
            f"job source_ref drift: requested {source_ref.lower()}, resolved {resolved_sha}"
        )

    required = [
        ".glaciereq/apex-authority.json",
        ".glaciereq/nervous-system.node.json",
        "scripts/validate_nervous_system.py",
        "scripts/validate_apex_runtime.py",
        "scripts/runtime_cli.py",
        "scripts/generate_runtime_projection.py",
        "scripts/verify_runtime_projection.py",
        "runtime/apex_contracts.py",
        "runtime/apex_hardening.py",
        "runtime/apex_receipts.py",
        "runtime/contracts/runtime_contract.json",
        "runtime/contracts/receipt.schema.json",
        "runtime/contracts/projection.schema.json",
        "tests/test_runtime_engine.py",
    ]
    missing = [rel for rel in required if not (root / rel).is_file()]
    if missing:
        raise RuntimeError(f"required APEX boot-core surfaces missing: {missing}")

    with tempfile.TemporaryDirectory(prefix="apex-boot-core-public-verify-") as td:
        tmp_root = Path(td)
        env = dict(env_builder(tmp_root, resolved_sha))
        projection = tmp_root / "projection"
        commands: list[tuple[str, list[str]]] = [
            (
                "nervous-system-authority",
                [sys.executable, "scripts/validate_nervous_system.py"],
            ),
            (
                "compile-runtime",
                [
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    "runtime",
                    "scripts/runtime_cli.py",
                    "scripts/generate_runtime_projection.py",
                    "scripts/verify_runtime_projection.py",
                    "scripts/validate_nervous_system.py",
                    "scripts/validate_apex_runtime.py",
                ],
            ),
            (
                "apex-runtime-authority-gate",
                [sys.executable, "scripts/validate_apex_runtime.py"],
            ),
            (
                "runtime-contract",
                [
                    sys.executable,
                    "scripts/runtime_cli.py",
                    "--source-revision",
                    resolved_sha,
                    "validate-contract",
                ],
            ),
            (
                "runtime-regressions",
                [sys.executable, "-m", "unittest", "-v", "tests/test_runtime_engine.py"],
            ),
            (
                "generate-source-projection",
                [
                    sys.executable,
                    "scripts/generate_runtime_projection.py",
                    "--dest",
                    str(projection),
                    "--source-revision",
                    resolved_sha,
                ],
            ),
            (
                "verify-source-projection",
                [
                    sys.executable,
                    "scripts/verify_runtime_projection.py",
                    "--root",
                    str(projection),
                    "--expect-source-revision",
                    resolved_sha,
                ],
            ),
        ]

        results: list[dict[str, Any]] = []
        for name, argv in commands:
            result = runner(name, argv, cwd=root, env=env)
            results.append(result)
            if not result.get("passed"):
                break

        passed = len(results) == len(commands) and all(bool(row.get("passed")) for row in results)
        projection_manifest = projection / "RUNTIME_PROJECTION.json"
        projection_sha = (
            hashlib.sha256(projection_manifest.read_bytes()).hexdigest()
            if projection_manifest.is_file()
            else None
        )
        return {
            "schema": RESULT_SCHEMA,
            "job_id": job_id,
            "pillar": "C",
            "action": ACTION_ID,
            "target_repository": TARGET_REPOSITORY,
            "source_repository": source_repository,
            "source_ref": source_ref,
            "resolved_source_sha": resolved_sha,
            "executed_source_sha": actual_sha,
            "mode": "APEX",
            "human_project_direction_authority": HUMAN_PROJECT_DIRECTION_AUTHORITY,
            "execution_law": EXECUTION_LAW,
            "projection_authority": "NON_AUTHORITY",
            "commands": results,
            "projection_manifest_sha256": projection_sha,
            "status": "passed" if passed else "failed",
        }


def main() -> int:
    raw = os.environ.get("APEX_ACTION_PLAN_JSON", "").strip()
    if not raw:
        raise SystemExit("APEX_ACTION_PLAN_JSON is required")
    plan = json.loads(raw)
    if not isinstance(plan, dict):
        raise SystemExit("APEX_ACTION_PLAN_JSON must contain an object")
    result = execute(plan=plan)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
