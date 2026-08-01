#!/usr/bin/env python3
"""Code-domain wrapper for the existing Monolith IP-governance adapter.

The temporary legacy alias preserves byte-for-byte result compatibility. The
canonical action executes the same legacy core, then converts its result into a
bounded, hash-verifiable domain receipt that excludes raw command output.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import apex_catalog_runner as catalog
import monolith_ip_governance_adapter as legacy

DOMAIN = "code"
CANONICAL_ACTION = "code.validate-governance"
LEGACY_ACTION = "monolith-ip-governance"
ADAPTER = "monolith_ip_governance"
LEGACY_ADAPTER = "monolith-ip-governance"
TARGET_REPOSITORY = "GlacierEQ/monolith"
TOKEN_PROFILE = "private-source-read"
RESULT_SCHEMA_VERSION = "1.0"
ALLOWED_ACTIONS = frozenset({CANONICAL_ACTION, LEGACY_ACTION})
ALLOWED_ADAPTERS = frozenset({ADAPTER, LEGACY_ADAPTER})
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RESULT_KEYS = frozenset(
    {
        "schema_version",
        "job_id",
        "domain",
        "action",
        "adapter",
        "adapter_sha256",
        "token_profile",
        "source_repo",
        "source_ref",
        "resolved_source_sha",
        "status",
        "checks",
        "artifact_references",
        "legacy_result_sha256",
        "test_count",
        "manifest_summary",
        "secret_scan",
        "parent_receipt_sha256",
        "reason",
        "receipt_sha256",
    }
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def adapter_control_hashes() -> dict[str, str]:
    """Bind both the migration wrapper and preserved execution core."""
    return {
        "domain_wrapper": sha256_file(Path(__file__)),
        "legacy_execution_core": sha256_file(Path(legacy.__file__)),
    }


def adapter_bundle_sha256() -> str:
    return canonical_sha256(adapter_control_hashes())


def validate_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("domain adapter plan must be a JSON object")

    domain = plan.get("domain")
    if domain not in (None, "", DOMAIN):
        raise ValueError("cross-domain execution is forbidden")

    action = plan.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("action is not registered to the Monolith code adapter")

    adapter = plan.get("adapter")
    if adapter not in (None, "", *ALLOWED_ADAPTERS):
        raise ValueError("caller-selected adapter is forbidden")

    for key in ("source_repo", "target_repo"):
        value = plan.get(key)
        if value not in (None, "", TARGET_REPOSITORY):
            raise ValueError(f"{key} is not the catalog-bound repository")

    task = plan.get("task")
    if task not in (None, "", "test"):
        raise ValueError("Monolith governance task must remain test")
    return plan


def blocked_plan(plan: object) -> dict[str, Any]:
    """Build the minimum safe legacy result identity from untrusted input."""
    source = plan if isinstance(plan, dict) else {}
    job_id = source.get("job_id")
    pillar = source.get("pillar")
    source_ref = source.get("source_ref")
    return {
        "job_id": (
            job_id
            if isinstance(job_id, str) and 8 <= len(job_id) <= 64
            else "invalid-domain-plan"
        ),
        "pillar": pillar if pillar == "D" else "D",
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "task": "test",
        "source_repo": TARGET_REPOSITORY,
        "source_ref": source_ref if isinstance(source_ref, str) else "",
        "target_repo": TARGET_REPOSITORY,
    }


def reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"result path contains a symlink: {absolute}")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(os.path.abspath(path))
    reject_symlink_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_symlink_components(path)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        reject_symlink_components(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_name(step: dict[str, Any], index: int) -> str:
    command = step.get("command")
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        if len(command) >= 3 and command[1] == "-m":
            return f"python-module:{command[2]}"
        if len(command) >= 2:
            return Path(command[1]).name or f"step-{index}"
    return f"step-{index}"


def bounded_checks(legacy_result: dict[str, Any]) -> list[dict[str, str]]:
    steps = legacy_result.get("steps")
    if not isinstance(steps, list):
        return []
    checks: list[dict[str, str]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError("legacy result contains a malformed step")
        status = step.get("status")
        output_sha256 = step.get("output_sha256")
        if status not in {"completed", "failed", "blocked"}:
            raise ValueError("legacy result contains an invalid step status")
        if not isinstance(output_sha256, str) or not SHA64.fullmatch(output_sha256):
            raise ValueError("legacy result contains an invalid output hash")
        checks.append(
            {
                "name": check_name(step, index),
                "status": status,
                "output_sha256": output_sha256,
            }
        )
    return checks


def artifact_references(
    plan: dict[str, Any], legacy_result: dict[str, Any], result_path: Path
) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    secret_scan = legacy_result.get("secret_scan")
    if isinstance(secret_scan, dict):
        report_hash = secret_scan.get("report_sha256")
        if isinstance(report_hash, str) and SHA64.fullmatch(report_hash):
            artifacts.append(
                {"kind": "secret-scan-report", "sha256": report_hash}
            )

    job_id = plan.get("job_id")
    if isinstance(job_id, str):
        receipt_path = result_path.parent / f"{job_id}.publication-receipt.json"
        if receipt_path.is_file() and not receipt_path.is_symlink():
            artifacts.append(
                {
                    "kind": "publication-receipt",
                    "sha256": sha256_file(receipt_path),
                }
            )
    return artifacts


def canonical_result(
    plan: dict[str, Any], legacy_result: object, result_path: Path
) -> dict[str, Any]:
    if not isinstance(legacy_result, dict):
        raise ValueError("legacy adapter result must be a JSON object")
    if legacy_result.get("job_id") != plan.get("job_id"):
        raise ValueError("legacy adapter result job_id mismatch")
    if legacy_result.get("source_repo") != TARGET_REPOSITORY:
        raise ValueError("legacy adapter result repository mismatch")
    if legacy_result.get("source_ref") != plan.get("source_ref"):
        raise ValueError("legacy adapter result source_ref mismatch")

    status = legacy_result.get("status")
    if status not in {"completed", "failed", "blocked"}:
        raise ValueError("legacy adapter result status is invalid")
    resolved_source_sha = legacy_result.get("resolved_source_sha")
    if not isinstance(resolved_source_sha, str):
        raise ValueError("legacy adapter result source SHA is invalid")
    if resolved_source_sha and not SHA40.fullmatch(resolved_source_sha):
        raise ValueError("legacy adapter result source SHA is invalid")

    test_count = legacy_result.get("test_count", 0)
    if not isinstance(test_count, int) or isinstance(test_count, bool) or test_count < 0:
        raise ValueError("legacy adapter result test count is invalid")

    manifest_summary = legacy_result.get("manifest_summary")
    secret_scan = legacy_result.get("secret_scan")
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "job_id": str(plan["job_id"]),
        "domain": DOMAIN,
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "adapter_sha256": adapter_bundle_sha256(),
        "token_profile": TOKEN_PROFILE,
        "source_repo": TARGET_REPOSITORY,
        "source_ref": str(plan.get("source_ref", "")),
        "resolved_source_sha": resolved_source_sha,
        "status": status,
        "checks": bounded_checks(legacy_result),
        "artifact_references": artifact_references(
            plan, legacy_result, result_path
        ),
        "legacy_result_sha256": canonical_sha256(legacy_result),
        "test_count": test_count,
        "manifest_summary": (
            manifest_summary if isinstance(manifest_summary, dict) else {}
        ),
        "secret_scan": secret_scan if isinstance(secret_scan, dict) else {},
        "parent_receipt_sha256": None,
        "reason": str(legacy_result.get("reason", ""))[:2048],
    }
    result["receipt_sha256"] = canonical_sha256(result)
    verify_canonical_result(result)
    return result


def canonical_blocked_result(plan: object, reason: str) -> dict[str, Any]:
    safe = blocked_plan(plan)
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "job_id": safe["job_id"],
        "domain": DOMAIN,
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "adapter_sha256": adapter_bundle_sha256(),
        "token_profile": TOKEN_PROFILE,
        "source_repo": TARGET_REPOSITORY,
        "source_ref": safe["source_ref"],
        "resolved_source_sha": "",
        "status": "blocked",
        "checks": [],
        "artifact_references": [],
        "legacy_result_sha256": None,
        "test_count": 0,
        "manifest_summary": {},
        "secret_scan": {},
        "parent_receipt_sha256": None,
        "reason": reason[:2048],
    }
    result["receipt_sha256"] = canonical_sha256(result)
    verify_canonical_result(result)
    return result


def verify_canonical_result(result: object) -> None:
    if not isinstance(result, dict):
        raise ValueError("canonical result must be a JSON object")
    if set(result) != RESULT_KEYS:
        raise ValueError("canonical result contains missing or unknown fields")

    supplied_hash = result.get("receipt_sha256")
    if not isinstance(supplied_hash, str) or not SHA64.fullmatch(supplied_hash):
        raise ValueError("canonical result receipt hash is invalid")
    unsigned = dict(result)
    del unsigned["receipt_sha256"]
    expected_hash = canonical_sha256(unsigned)
    if not hmac.compare_digest(supplied_hash, expected_hash):
        raise ValueError("canonical result receipt hash mismatch")

    exact = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "domain": DOMAIN,
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "token_profile": TOKEN_PROFILE,
        "source_repo": TARGET_REPOSITORY,
    }
    for key, expected in exact.items():
        if result.get(key) != expected:
            raise ValueError(f"canonical result {key} mismatch")

    adapter_hash = result.get("adapter_sha256")
    if not isinstance(adapter_hash, str) or not SHA64.fullmatch(adapter_hash):
        raise ValueError("canonical result adapter hash is invalid")
    status = result.get("status")
    if status not in {"completed", "failed", "blocked"}:
        raise ValueError("canonical result status is invalid")

    resolved_source_sha = result.get("resolved_source_sha")
    if not isinstance(resolved_source_sha, str):
        raise ValueError("canonical result source SHA is invalid")
    if resolved_source_sha and not SHA40.fullmatch(resolved_source_sha):
        raise ValueError("canonical result source SHA is invalid")

    checks = result.get("checks")
    artifacts = result.get("artifact_references")
    if not isinstance(checks, list) or not isinstance(artifacts, list):
        raise ValueError("canonical result collections are malformed")
    if status == "completed":
        if not SHA40.fullmatch(resolved_source_sha):
            raise ValueError("completed canonical result lacks a source SHA")
        if not checks:
            raise ValueError("completed canonical result contains no checks")
        if result.get("test_count", 0) < 1:
            raise ValueError("completed canonical result executed zero tests")
        secret_scan = result.get("secret_scan")
        if not isinstance(secret_scan, dict) or secret_scan.get("status") != "passed":
            raise ValueError("completed canonical result lacks a passing secret scan")


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    """Validate boundaries, preserve alias parity, and emit canonical receipts."""
    try:
        validated = validate_plan(plan)
    except ValueError as error:
        if isinstance(plan, dict) and plan.get("action") == CANONICAL_ACTION:
            atomic_write_json(
                result_path,
                canonical_blocked_result(plan, str(error)),
            )
            return 2
        return catalog.write_result(
            blocked_plan(plan),
            result_path,
            "blocked",
            reason=str(error),
            domain=DOMAIN,
            canonical_action=CANONICAL_ACTION,
        )

    if validated["action"] == LEGACY_ACTION:
        return legacy.run(validated, workspace, result_path)

    result_path = Path(os.path.abspath(result_path))
    result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, internal_name = tempfile.mkstemp(
        prefix=f".{validated['job_id']}.legacy.",
        suffix=".json",
        dir=result_path.parent,
    )
    os.close(descriptor)
    internal_result = Path(internal_name)
    internal_result.unlink()
    try:
        legacy.run(validated, workspace, internal_result)
        legacy_value = json.loads(internal_result.read_text(encoding="utf-8"))
        result = canonical_result(validated, legacy_value, result_path)
        atomic_write_json(result_path, result)
        return 0 if result["status"] == "completed" else 2
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        atomic_write_json(
            result_path,
            canonical_blocked_result(
                validated,
                f"canonical result conversion failed: {type(error).__name__}: {error}",
            ),
        )
        return 2
    finally:
        internal_result.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: monolith_ip_governance.py PLAN WORKSPACE RESULT"
        )
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return run(plan, workspace, result_path)


if __name__ == "__main__":
    raise SystemExit(main())
