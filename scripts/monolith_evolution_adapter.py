"""Generate Monolith evolution ledgers and seal them into a private receipt.

The workload token is read-only and is removed from the subprocess environment.
Generated files never enter a public artifact or public log. They are compressed,
hashed, and returned only through the action face's immutable private receipt.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import apex_catalog_runner as catalog

SENSITIVE_ENV = {
    "APEX_BRANCH_WRITE_TOKEN",
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "GH_PAT",
    "GITHUB_TOKEN",
}
MAX_COMMAND_OUTPUT = 12_000
MAX_PRIVATE_PAYLOAD_BYTES = 4_500_000
TEST_COUNT = re.compile(r"Ran\s+(\d+)\s+tests?", re.IGNORECASE)


def isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in SENSITIVE_ENV:
        env.pop(key, None)
    env.update(
        {
            "CI": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    return env


def run_command(
    command: list[str], workspace: Path, timeout: int = 900
) -> dict[str, Any]:
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
            "output_tail": output[-MAX_COMMAND_OUTPUT:],
        }
    except subprocess.TimeoutExpired as error:
        output = error.stdout if isinstance(error.stdout, str) else ""
        return {
            "command": command,
            "exit_code": None,
            "status": "failed",
            "reason": f"timeout after {timeout} seconds",
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_tail": output[-MAX_COMMAND_OUTPUT:],
        }
    except OSError as error:
        return {
            "command": command,
            "exit_code": None,
            "status": "failed",
            "reason": f"process start failed: {type(error).__name__}: {error}",
            "output_tail": "",
        }


def seal_artifact(path: Path, workspace: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    return {
        "path": path.relative_to(workspace).as_posix(),
        "encoding": "gzip+base64",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "gzip_bytes": len(compressed),
        "gzip_sha256": hashlib.sha256(compressed).hexdigest(),
        "data_base64": base64.b64encode(compressed).decode("ascii"),
    }


def validate_ledger(payload: Any) -> tuple[int, dict[str, int]]:
    if not isinstance(payload, dict):
        raise TypeError("evolution_map.json must contain a JSON object")
    source = payload.get("source_catalog")
    records = payload.get("records")
    counts = payload.get("counts")
    generation_counts = counts.get("generation") if isinstance(counts, dict) else None
    if not isinstance(source, dict) or not isinstance(records, list):
        raise TypeError("evolution ledger lacks source_catalog or records")
    if not isinstance(generation_counts, dict):
        raise TypeError("evolution ledger lacks generation counts")
    entry_count = source.get("entry_count")
    if not isinstance(entry_count, int) or entry_count < 1:
        raise ValueError("source entry_count is invalid")
    if len(records) != entry_count:
        raise ValueError("record count does not match source entry_count")
    normalized: dict[str, int] = {}
    for key, value in generation_counts.items():
        if not isinstance(key, str) or not isinstance(value, int) or value < 0:
            raise TypeError("generation counts contain an invalid entry")
        normalized[key] = value
    if sum(normalized.values()) != entry_count:
        raise ValueError("generation counts do not sum to source entry_count")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TypeError(f"record {index} is not an object")
        for field in (
            "name",
            "domain",
            "function_category",
            "generation",
            "generation_reason",
            "system_level",
            "system_level_reason",
        ):
            if not record.get(field):
                raise ValueError(f"record {index} lacks {field}")
    return entry_count, normalized


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    required = [
        workspace / "catalog" / "library.json",
        workspace / "scripts" / "evolution.py",
        workspace / "scripts" / "generate_evolution_map.py",
        workspace / "tests" / "test_evolution.py",
    ]
    missing = [
        path.relative_to(workspace).as_posix()
        for path in required
        if not path.is_file()
    ]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"required Monolith files are missing: {', '.join(missing)}",
        )

    commands = [
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
        ],
        [sys.executable, "scripts/generate_evolution_map.py"],
        [sys.executable, "scripts/generate_evolution_map.py", "--check"],
    ]
    steps: list[dict[str, Any]] = []
    for command in commands:
        step = run_command(command, workspace)
        steps.append(step)
        if step["status"] != "completed":
            return catalog.write_result(
                plan,
                result_path,
                "failed",
                reason="Monolith evolution command failed",
                steps=steps,
            )

    json_path = workspace / "catalog" / "evolution_map.json"
    markdown_path = workspace / "status" / "EVOLUTION_LEVELS.md"
    if not json_path.is_file() or not markdown_path.is_file():
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason="generator completed without producing both governed ledgers",
            steps=steps,
        )

    try:
        ledger = json.loads(json_path.read_text(encoding="utf-8"))
        source_entry_count, generation_counts = validate_ledger(ledger)
        artifacts = {
            "catalog/evolution_map.json": seal_artifact(json_path, workspace),
            "status/EVOLUTION_LEVELS.md": seal_artifact(markdown_path, workspace),
        }
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason=f"generated ledger validation failed: {type(error).__name__}: {error}",
            steps=steps,
        )

    test_match = TEST_COUNT.search(steps[0].get("output_tail", ""))
    test_count = int(test_match.group(1)) if test_match else None
    details = {
        "steps": steps,
        "test_count": test_count,
        "source_entry_count": source_entry_count,
        "generation_counts": generation_counts,
        "artifacts": artifacts,
    }
    estimated = len(json.dumps(details, separators=(",", ":")).encode("utf-8"))
    if estimated > MAX_PRIVATE_PAYLOAD_BYTES:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=(
                "compressed evolution ledgers exceed the bounded private receipt "
                f"payload ({estimated} > {MAX_PRIVATE_PAYLOAD_BYTES} bytes)"
            ),
            steps=steps,
            source_entry_count=source_entry_count,
            generation_counts=generation_counts,
            artifact_metadata={
                name: {
                    key: value
                    for key, value in artifact.items()
                    if key != "data_base64"
                }
                for name, artifact in artifacts.items()
            },
        )

    return catalog.write_result(plan, result_path, "completed", **details)
