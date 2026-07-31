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
from collections import Counter
from pathlib import Path
from typing import Any

import apex_catalog_runner as catalog

SENSITIVE_ENV = {
    "AKOS_POLICY_SHA256",
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


def parse_test_count(output: str) -> int:
    match = TEST_COUNT.search(output)
    if not match:
        raise ValueError("unittest output did not report an executed test count")
    count = int(match.group(1))
    if count < 1:
        raise ValueError("unittest discovery executed zero tests")
    return count


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


def validate_source_catalog(payload: Any) -> tuple[list[dict[str, Any]], bytes]:
    if not isinstance(payload, dict):
        raise TypeError("catalog/library.json must contain a JSON object")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise TypeError("catalog/library.json must contain a nonempty entries array")
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TypeError(f"source catalog entry {index} is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"source catalog entry {index} lacks a valid name")
        if name in names:
            raise ValueError(
                f"source catalog contains duplicate repository name: {name}"
            )
        names.add(name)
        normalized.append(entry)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return normalized, raw


def validate_ledger(
    payload: Any,
    source_catalog: dict[str, Any],
) -> tuple[int, dict[str, int]]:
    if not isinstance(payload, dict):
        raise TypeError("evolution_map.json must contain a JSON object")
    source_entries, _ = validate_source_catalog(source_catalog)
    source_by_name = {str(entry["name"]): entry for entry in source_entries}

    source = payload.get("source_catalog")
    records = payload.get("records")
    counts = payload.get("counts")
    generation_counts = counts.get("generation") if isinstance(counts, dict) else None
    if not isinstance(source, dict) or not isinstance(records, list):
        raise TypeError("evolution ledger lacks source_catalog or records")
    if not isinstance(generation_counts, dict):
        raise TypeError("evolution ledger lacks generation counts")

    entry_count = len(source_entries)
    if source.get("path") != "catalog/library.json":
        raise ValueError("evolution ledger source path is invalid")
    if source.get("entry_count") != entry_count:
        raise ValueError("evolution ledger source count differs from library.json")
    if source.get("version") != source_catalog.get("version"):
        raise ValueError("evolution ledger source version differs from library.json")
    if source.get("updated") != source_catalog.get("updated"):
        raise ValueError("evolution ledger source timestamp differs from library.json")
    if len(records) != entry_count:
        raise ValueError("record count does not match source catalog")

    normalized_counts: dict[str, int] = {}
    for key, value in generation_counts.items():
        if not isinstance(key, str) or not isinstance(value, int) or value < 0:
            raise TypeError("generation counts contain an invalid entry")
        normalized_counts[key] = value
    if sum(normalized_counts.values()) != entry_count:
        raise ValueError("generation counts do not sum to source entry_count")

    record_names: set[str] = set()
    derived_counts: Counter[str] = Counter()
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
        name = str(record["name"])
        if name in record_names:
            raise ValueError(f"evolution ledger contains duplicate repository: {name}")
        record_names.add(name)
        source_entry = source_by_name.get(name)
        if source_entry is None:
            raise ValueError(f"evolution ledger contains unknown repository: {name}")
        for key, expected in source_entry.items():
            if record.get(key) != expected:
                raise ValueError(f"evolution record {name} changed source field {key}")
        derived_counts[str(record["generation"])] += 1

    if record_names != set(source_by_name):
        missing = sorted(set(source_by_name) - record_names)
        raise ValueError(
            f"evolution ledger omitted repositories: {', '.join(missing[:10])}"
        )
    declared_nonzero = {
        key: value for key, value in normalized_counts.items() if value != 0
    }
    if dict(derived_counts) != declared_nonzero:
        raise ValueError("declared generation counts do not match record generations")
    return entry_count, normalized_counts


def validate_markdown(
    text: str,
    entry_count: int,
    generation_counts: dict[str, int],
) -> None:
    if not text.strip():
        raise ValueError("status/EVOLUTION_LEVELS.md is empty")
    required = {
        "# Evolution Levels",
        "## Complete repository placement",
        "## Regenerate and verify",
        f"**Repositories classified:** {entry_count}",
    }
    required.update(f"### {generation}" for generation in generation_counts)
    missing = sorted(fragment for fragment in required if fragment not in text)
    if missing:
        raise ValueError(
            "status/EVOLUTION_LEVELS.md lacks required structure: " + ", ".join(missing)
        )


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    catalog_path = workspace / "catalog" / "library.json"
    required = [
        catalog_path,
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

    try:
        source_bytes = catalog_path.read_bytes()
        source_catalog = json.loads(source_bytes.decode("utf-8"))
        validate_source_catalog(source_catalog)
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
            "blocked",
            reason=f"source catalog validation failed: {type(error).__name__}: {error}",
        )

    test_step = run_command(
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
        workspace,
    )
    steps: list[dict[str, Any]] = [test_step]
    if test_step["status"] != "completed":
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason="Monolith unittest command failed",
            steps=steps,
        )
    try:
        test_count = parse_test_count(test_step.get("output_tail", ""))
    except ValueError as error:
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason=str(error),
            steps=steps,
        )

    for command in (
        [sys.executable, "scripts/generate_evolution_map.py"],
        [sys.executable, "scripts/generate_evolution_map.py", "--check"],
    ):
        step = run_command(command, workspace)
        steps.append(step)
        if step["status"] != "completed":
            return catalog.write_result(
                plan,
                result_path,
                "failed",
                reason="Monolith evolution command failed",
                steps=steps,
                test_count=test_count,
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
            test_count=test_count,
        )

    try:
        ledger = json.loads(json_path.read_text(encoding="utf-8"))
        source_entry_count, generation_counts = validate_ledger(ledger, source_catalog)
        markdown = markdown_path.read_text(encoding="utf-8")
        validate_markdown(markdown, source_entry_count, generation_counts)
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
            test_count=test_count,
        )

    details = {
        "steps": steps,
        "test_count": test_count,
        "source_entry_count": source_entry_count,
        "source_catalog_sha256": hashlib.sha256(source_bytes).hexdigest(),
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
            test_count=test_count,
            source_entry_count=source_entry_count,
            source_catalog_sha256=hashlib.sha256(source_bytes).hexdigest(),
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
