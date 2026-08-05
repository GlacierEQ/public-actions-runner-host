#!/usr/bin/env python3
"""Run Monolith's commit-bound IP and legal governance gate.

The adapter is read-only with respect to the workload. It executes the exact
repository-owned overlay, scanner, schema-backed manifest validator, bounded
legal-authorization validator, substantive release-evidence gate,
publication-receipt readiness gate, and tests; proves that at least one test
ran; and returns only bounded verification metadata through the private result
plane. Critical control bytes are hashed before execution and must remain
unchanged afterward.
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
SHA40 = re.compile(r"^[0-9a-f]{40}$")
LEGAL_AUTHORIZATION_EXAMPLE_PATH = (
    "examples/legal/evaluation-authorization.example.json"
)
LEGAL_AUTHORIZATION_SCHEMA_PATH = "schemas/legal-authorization.schema.json"
LEGAL_AUTHORIZATION_VALIDATOR_PATH = "scripts/validate_legal_authorization.py"
CRITICAL_PATHS = (
    "ip-manifest.json",
    "catalog/rights_overlay.json",
    LEGAL_AUTHORIZATION_EXAMPLE_PATH,
    "schemas/ip-manifest.schema.json",
    LEGAL_AUTHORIZATION_SCHEMA_PATH,
    "scripts/generate_publication_receipt.py",
    "scripts/json_schema_subset.py",
    "scripts/load_governed_catalog.py",
    "scripts/scan_secrets.py",
    "scripts/validate_evidence_records.py",
    "scripts/validate_ip_manifest.py",
    LEGAL_AUTHORIZATION_VALIDATOR_PATH,
    "scripts/validate_publication_authorization.py",
    "scripts/validate_release_evidence.py",
    "scripts/verify_publication_readiness.py",
)


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


def hash_critical_files(workspace: Path) -> dict[str, str]:
    return {
        relative: sha256_file(workspace / relative) for relative in CRITICAL_PATHS
    }


def manifest_summary(manifest: object) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise TypeError("ip-manifest.json must contain a JSON object")
    repository = manifest.get("repository")
    publication = manifest.get("publication")
    approval = manifest.get("releaseApproval")
    authorship = manifest.get("authorship")
    return {
        "schema_version": manifest.get("schemaVersion"),
        "repository": (
            repository.get("fullName") if isinstance(repository, dict) else None
        ),
        "visibility": (
            repository.get("visibility") if isinstance(repository, dict) else None
        ),
        "rights_status": manifest.get("rightsStatus"),
        "publication_class": (
            publication.get("class") if isinstance(publication, dict) else None
        ),
        "release_status": (
            approval.get("status") if isinstance(approval, dict) else None
        ),
        "ai_assistance": (
            authorship.get("aiAssistance") if isinstance(authorship, dict) else None
        ),
        "human_review_status": (
            authorship.get("humanReviewStatus")
            if isinstance(authorship, dict)
            else None
        ),
    }


def scan_summary(report: object) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise TypeError("secret scan report must contain a JSON object")
    return {
        "scanner": report.get("scanner"),
        "status": report.get("status"),
        "scanned_commit": report.get("scannedCommit"),
        "files_tracked": report.get("filesTracked"),
        "files_scanned": report.get("filesScanned"),
        "files_skipped": report.get("filesSkipped"),
        "finding_count": report.get("findingCount"),
        "report_sha256": report.get("reportSha256"),
    }


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    resolved_source_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "")
    if not SHA40.fullmatch(resolved_source_sha):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="APEX_RESOLVED_SOURCE_SHA is unavailable or invalid",
        )

    required = [*(workspace / path for path in CRITICAL_PATHS), workspace / "tests"]
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
        critical_files_before = hash_critical_files(workspace)
        manifest = json.loads((workspace / "ip-manifest.json").read_text(encoding="utf-8"))
        manifest_schema = json.loads(
            (workspace / "schemas" / "ip-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        legal_schema = json.loads(
            (workspace / LEGAL_AUTHORIZATION_SCHEMA_PATH).read_text(encoding="utf-8")
        )
        legal_example = json.loads(
            (workspace / LEGAL_AUTHORIZATION_EXAMPLE_PATH).read_text(encoding="utf-8")
        )
        if not isinstance(manifest_schema, dict):
            raise TypeError("IP manifest schema must contain a JSON object")
        if not isinstance(legal_schema, dict):
            raise TypeError("legal authorization schema must contain a JSON object")
        if not isinstance(legal_example, dict):
            raise TypeError("legal authorization example must contain a JSON object")
        summary = manifest_summary(manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"governance input validation failed: {type(error).__name__}: {error}",
        )

    scan_path = result_path.parent / f"{plan['job_id']}.secret-scan.json"
    receipt_path = result_path.parent / f"{plan['job_id']}.publication-receipt.json"
    commands = [
        [sys.executable, "scripts/load_governed_catalog.py"],
        [
            sys.executable,
            "scripts/scan_secrets.py",
            "--root",
            ".",
            "--commit",
            resolved_source_sha,
            "--output",
            str(scan_path),
        ],
        [
            sys.executable,
            "scripts/validate_ip_manifest.py",
            "ip-manifest.json",
            "--schema",
            "schemas/ip-manifest.schema.json",
            "--secret-scan-report",
            str(scan_path),
            "--expected-commit",
            resolved_source_sha,
        ],
        [
            sys.executable,
            LEGAL_AUTHORIZATION_VALIDATOR_PATH,
            LEGAL_AUTHORIZATION_EXAMPLE_PATH,
            "--schema",
            LEGAL_AUTHORIZATION_SCHEMA_PATH,
        ],
        [sys.executable, "scripts/validate_release_evidence.py", "ip-manifest.json"],
        [
            sys.executable,
            "scripts/verify_publication_readiness.py",
            "--manifest",
            "ip-manifest.json",
            "--secret-scan-report",
            str(scan_path),
            "--attestation-commit",
            resolved_source_sha,
            "--output",
            str(receipt_path),
        ],
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
        [sys.executable, "-m", "json.tool", str(scan_path)],
    ]

    steps: list[dict[str, Any]] = []
    test_count = 0
    for index, command in enumerate(commands):
        step = run_command(command, workspace)
        steps.append(step)

        if index == 6:
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
                failed_step=index + 1,
                steps=steps,
                manifest_summary=summary,
            )

    try:
        critical_files_after = hash_critical_files(workspace)
    except OSError as error:
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason=f"critical governance files became unreadable: {error}",
            steps=steps,
            manifest_summary=summary,
        )
    if critical_files_after != critical_files_before:
        changed = sorted(
            path
            for path in CRITICAL_PATHS
            if critical_files_after.get(path) != critical_files_before.get(path)
        )
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason="critical governance files changed during execution",
            changed_critical_files=changed,
            steps=steps,
            manifest_summary=summary,
        )

    try:
        report = json.loads(scan_path.read_text(encoding="utf-8"))
        bounded_scan = scan_summary(report)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason=f"secret scan result could not be summarized: {type(error).__name__}: {error}",
            steps=steps,
            manifest_summary=summary,
        )

    return catalog.write_result(
        plan,
        result_path,
        "completed",
        resolved_source_sha=resolved_source_sha,
        test_count=test_count,
        manifest_summary=summary,
        secret_scan=bounded_scan,
        critical_file_sha256=critical_files_before,
        steps=steps,
    )
