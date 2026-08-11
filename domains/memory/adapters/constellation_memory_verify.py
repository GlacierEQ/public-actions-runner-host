"""Memory-domain adapter for exact-SHA Constellation OperatorCode verification."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import apex_catalog_runner as catalog

EXPECTED_ACTION = "memory.constellation.verify-operator-code"
EXPECTED_REPOSITORY = "GlacierEQ/constellation-memory-engine"
SHA = re.compile(r"^[0-9a-f]{40}$")


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    if plan.get("action") != EXPECTED_ACTION:
        return catalog.write_result(plan, result_path, "blocked", reason="action identity mismatch")
    if plan.get("source_repo") != EXPECTED_REPOSITORY:
        return catalog.write_result(plan, result_path, "blocked", reason="repository identity mismatch")
    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "")
    if not SHA.fullmatch(resolved_sha) or plan.get("source_ref") != resolved_sha:
        return catalog.write_result(plan, result_path, "blocked", reason="exact source SHA binding failed")
    gate = workspace / "scripts" / "ci" / "verify.sh"
    if not gate.is_file() or gate.is_symlink():
        return catalog.write_result(plan, result_path, "blocked", reason="repository-owned verification gate is unavailable")
    command = ["bash", str(gate.resolve())]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "CI": "true",
        "GITHUB_REPOSITORY": EXPECTED_REPOSITORY,
        "GITHUB_SHA": resolved_sha,
        "APEX_RESOLVED_SOURCE_SHA": resolved_sha,
    }
    process = subprocess.run(
        command,
        cwd=workspace,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1800,
        check=False,
        shell=False,
    )
    output = (process.stdout or "")[-100000:]
    return catalog.write_result(
        plan,
        result_path,
        "completed" if process.returncode == 0 else "failed",
        steps=[{
            "command": command,
            "exit_code": process.returncode,
            "status": "completed" if process.returncode == 0 else "failed",
            "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
            "output_tail": output[-24000:],
        }],
        expected_source_sha=resolved_sha,
        command_contract_sha256=hashlib.sha256(repr(command).encode()).hexdigest(),
    )
