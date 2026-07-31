#!/usr/bin/env python3
"""Run the Monolith IP governance gate through the public/private action face.

The adapter is read-only with respect to the workload. It executes the exact
repository-owned validator and tests, proves that at least one test ran, checks
critical JSON syntax, and returns only bounded verification metadata through
the private receipt plane.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import apex_catalog_runner as catalog

ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
MAX_OUTPUT = 24_000
TEST_COUNT = re.compile(r"Ran\s+(\d+)\s+tests?", re.IGNORECASE)


def isolated_env() -> dict[str, str]:
    env = {
        key: value
        for key in ENV_ALLOWLIST
        if (value := os.environ.get(key))
    }
    env.update(
        {
            "CI": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def run_command(command: list[str], workspace: Path, timeout: int = 900) -> dict[str, Any]:
    try:
        process = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=isolated_env(),
        )
        output = process.stdout or ""
        return {
            "command": command,
            "exit_code": process.returncode,
            "status": "completed" if process.returncode == 0 else "failed",
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_tail": output[-MAX_OUTPUT:],
        }
    except subprocess.TimeoutExpired as error:
        output = error.stdout if isinstance(error.stdout, str) else ""
        return {
            "command": command,
            "exit_code": None,
            "status": "failed",
            "reason": f"timeout after {timeout} seconds",
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_tail": output[-MAX_OUTPUT:],
        }
    except OSError as error:
        return {
            "command": command,
            "exit_code": None,
            "status": "failed",
            "reason": f"process start failed: {type(error).__name__}: {error}",
            "output_tail": "",
        }


def parse_test_count(output: str) -> int:
    match = TEST_COUNT.search(output)
    if not match:
        raise ValueError("unittest output did not report an executed test count")
    count = int(match.group(1))
    if count < 1:
        raise ValueError("unittest discovery executed zero tests")
    return count


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_summary(manifest: object) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise TypeError("ip-manifest.json must contain a JSON object")
    rights = manifest.get("rights")
    publication = manifest.get("publication")
    return {
        "schema_version": manifest.get("schemaVersion") or manifest.get("schema_version"),
        "repository": manifest.get("repository"),
        "rights_status": rights.get("status") if isinstance(rights, dict) else None,
        "publication_authorization": (
            publication.get("authorization") if isinstance(publication, dict) else None
        ),
    }


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()

    required = [
        workspace / "ip-manifest.json",
        workspace / "schemas" / "ip-manifest.schema.json",
        workspace / "scripts" / "validate_ip_manifest.py",
        workspace / "tests",
    ]
    missing = [
        path.relative_to(workspace).as_posix()
        for path in required
        if not path.exists()
    ]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"required Monolith IP governance paths are missing: {', '.join(missing)}",
        )

    try:
        manifest = json.loads((workspace / "ip-manifest.json").read_text(encoding="utf-8"))
        schema = json.loads(
            (workspace / "schemas" / "ip-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(schema, dict):
            raise TypeError("IP manifest schema must contain a JSON object")
        summary = manifest_summary(manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"governance input validation failed: {type(error).__name__}: {error}",
        )

    commands = [
        [sys.executable, "scripts/validate_ip_manifest.py", "ip-manifest.json"],
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
            "-v",
        ],
        [sys.executable, "-m", "json.tool", "ip-manifest.json"],
        [
            sys.executable,
            "-m",
            "json.tool",
            "schemas/ip-manifest.schema.json",
        ],
    ]

    steps: list[dict[str, Any]] = []
    test_count = 0
    for index, command in enumerate(commands):
        step = run_command(command, workspace)
        steps.append(step)

        if index == 1:
            try:
                test_count = parse_test_count(step.get("output_tail", ""))
            except ValueError as error:
                return catalog.write_result(
                    plan,
                    result_path,
                    "failed",
                    reason=str(error),
                    steps=steps,
                    manifest_summary=summary,
                )

        if step["status"] != "completed":
            return catalog.write_result(
                plan,
                result_path,
                "failed",
                reason="Monolith IP governance command failed",
                steps=steps,
                manifest_summary=summary,
            )

    critical_files = {
        "ip-manifest.json": sha256_file(workspace / "ip-manifest.json"),
        "schemas/ip-manifest.schema.json": sha256_file(
            workspace / "schemas" / "ip-manifest.schema.json"
        ),
        "scripts/validate_ip_manifest.py": sha256_file(
            workspace / "scripts" / "validate_ip_manifest.py"
        ),
    }

    return catalog.write_result(
        plan,
        result_path,
        "completed",
        test_count=test_count,
        manifest_summary=summary,
        critical_file_sha256=critical_files,
        steps=steps,
    )
