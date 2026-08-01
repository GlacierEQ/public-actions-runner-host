#!/usr/bin/env python3
"""Code-domain wrapper for the existing Monolith IP-governance adapter.

The temporary legacy alias preserves byte-for-byte result compatibility. A
schema-valid canonical envelope is normalized into the fixed legacy execution
shape, executed by the existing core, and converted into a bounded,
hash-verifiable domain receipt that excludes raw command output.
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
ALLOWED_STATUSES = frozenset({"completed", "failed", "blocked"})
ALLOWED_ARTIFACT_KINDS = frozenset(
    {"secret-scan-report", "publication-receipt"}
)
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
LEGACY_JOB_KEYS = frozenset(
    {
        "job_id",
        "pillar",
        "action",
        "adapter",
        "task",
        "source_repo",
        "source_ref",
        "target_repo",
    }
)
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
SOURCE_REF = re.compile(
    r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,128}$"
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "repository",
        "visibility",
        "rights_status",
        "publication_class",
        "release_status",
        "ai_assistance",
        "human_review_status",
    }
)
SECRET_SCAN_KEYS = frozenset(
    {
        "scanner",
        "status",
        "scanned_commit",
        "files_tracked",
        "files_scanned",
        "files_skipped",
        "finding_count",
        "report_sha256",
    }
)
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
        "expected_source_sha",
        "approval_id",
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


def valid_source_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(SOURCE_REF.fullmatch(value))
        and not value.endswith("/")
        and not value.endswith(".lock")
    )


def validate_expected_source_sha(plan: dict[str, Any]) -> str | None:
    expected = plan.get("expected_source_sha")
    if expected in (None, ""):
        return None
    if not isinstance(expected, str) or not SHA40.fullmatch(expected):
        raise ValueError("expected_source_sha is invalid")
    resolved = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "")
    if SHA40.fullmatch(resolved) and not hmac.compare_digest(expected, resolved):
        raise ValueError("expected_source_sha does not match resolved source")
    return expected


def validate_approval_id(plan: dict[str, Any]) -> str | None:
    approval_id = plan.get("approval_id")
    if approval_id in (None, ""):
        return None
    if not isinstance(approval_id, str) or not JOB_ID.fullmatch(approval_id):
        raise ValueError("approval_id is invalid")
    return approval_id


def validate_common_job(plan: dict[str, Any]) -> None:
    job_id = plan.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("job_id is invalid")
    if not valid_source_ref(plan.get("source_ref")):
        raise ValueError("source_ref is invalid")


def normalize_canonical_plan(plan: dict[str, Any]) -> dict[str, Any]:
    unknown = set(plan) - CANONICAL_JOB_KEYS
    if unknown:
        raise ValueError("canonical plan contains unsupported fields")
    validate_common_job(plan)
    if plan.get("domain") != DOMAIN:
        raise ValueError("canonical plan domain must be code")
    if plan.get("action") != CANONICAL_ACTION:
        raise ValueError("canonical action is invalid")

    expected_source_sha = validate_expected_source_sha(plan)
    approval_id = validate_approval_id(plan)
    normalized: dict[str, Any] = {
        "job_id": plan["job_id"],
        "pillar": "D",
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "task": "test",
        "source_repo": TARGET_REPOSITORY,
        "source_ref": plan["source_ref"],
        "target_repo": TARGET_REPOSITORY,
        "expected_source_sha": expected_source_sha,
        "approval_id": approval_id,
    }
    return normalized


def validate_legacy_plan(plan: dict[str, Any]) -> dict[str, Any]:
    unknown = set(plan) - LEGACY_JOB_KEYS
    if unknown:
        raise ValueError("legacy plan contains unsupported fields")
    validate_common_job(plan)
    if plan.get("pillar") != "D":
        raise ValueError("Monolith governance pillar must remain D")
    if plan.get("action") != LEGACY_ACTION:
        raise ValueError("legacy action is invalid")
    if plan.get("adapter") != LEGACY_ADAPTER:
        raise ValueError("legacy adapter is invalid")
    if plan.get("task") != "test":
        raise ValueError("Monolith governance task must remain test")
    for key in ("source_repo", "target_repo"):
        if plan.get(key) != TARGET_REPOSITORY:
            raise ValueError(f"{key} is not the catalog-bound repository")
    return plan


def validate_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("domain adapter plan must be a JSON object")
    action = plan.get("action")
    if action == CANONICAL_ACTION:
        return normalize_canonical_plan(plan)
    if action == LEGACY_ACTION:
        return validate_legacy_plan(plan)
    raise ValueError("action is not registered to the Monolith code adapter")


def blocked_plan(plan: object) -> dict[str, Any]:
    """Build the minimum safe legacy result identity from untrusted input."""
    source = plan if isinstance(plan, dict) else {}
    job_id = source.get("job_id")
    source_ref = source.get("source_ref")
    return {
        "job_id": (
            job_id
            if isinstance(job_id, str) and JOB_ID.fullmatch(job_id)
            else "invalid-domain-plan"
        ),
        "pillar": "D",
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "task": "test",
        "source_repo": TARGET_REPOSITORY,
        "source_ref": source_ref if valid_source_ref(source_ref) else "",
        "target_repo": TARGET_REPOSITORY,
    }


def safe_optional_sha(plan: object, key: str) -> str | None:
    if not isinstance(plan, dict):
        return None
    value = plan.get(key)
    return value if isinstance(value, str) and SHA40.fullmatch(value) else None


def safe_optional_id(plan: object, key: str) -> str | None:
    if not isinstance(plan, dict):
        return None
    value = plan.get(key)
    return value if isinstance(value, str) and JOB_ID.fullmatch(value) else None


def reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"result path contains a symlink: {absolute}")


def prepare_result_directory(result_path: Path) -> Path:
    result_path = Path(os.path.abspath(result_path))
    reject_symlink_components(result_path.parent)
    result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_symlink_components(result_path.parent)
    reject_symlink_components(result_path)
    return result_path


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path = prepare_result_directory(path)
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
            return f"python-module:{command[2]}"[:128]
        if len(command) >= 2:
            return (Path(command[1]).name or f"step-{index}")[:128]
    return f"step-{index}"


def bounded_checks(legacy_result: dict[str, Any]) -> list[dict[str, str]]:
    steps = legacy_result.get("steps")
    if not isinstance(steps, list):
        return []
    if len(steps) > 64:
        raise ValueError("legacy result contains too many steps")

    checks: list[dict[str, str]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError("legacy result contains a malformed step")
        status = step.get("status")
        output_sha256 = step.get("output_sha256")
        if status not in ALLOWED_STATUSES:
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
    if isinstance(job_id, str) and JOB_ID.fullmatch(job_id):
        receipt_path = result_path.parent / f"{job_id}.publication-receipt.json"
        reject_symlink_components(receipt_path)
        if receipt_path.is_file():
            artifacts.append(
                {
                    "kind": "publication-receipt",
                    "sha256": sha256_file(receipt_path),
                }
            )
    return artifacts


def validate_bounded_mapping(
    value: object,
    *,
    name: str,
    allowed_keys: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"canonical result {name} must be an object")
    unknown = set(value) - allowed_keys
    if unknown:
        raise ValueError(f"canonical result {name} contains unknown fields")
    return value


def normalize_manifest_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    validated = validate_bounded_mapping(
        value,
        name="manifest_summary",
        allowed_keys=MANIFEST_KEYS,
    )
    return dict(validated)


def normalize_secret_scan(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    validated = validate_bounded_mapping(
        value,
        name="secret_scan",
        allowed_keys=SECRET_SCAN_KEYS,
    )
    normalized = dict(validated)
    for key in ("scanned_commit", "report_sha256"):
        if normalized.get(key) == "":
            normalized[key] = None
    return normalized


def canonical_result(
    plan: dict[str, Any], legacy_result: object, result_path: Path
) -> dict[str, Any]:
    if not isinstance(legacy_result, dict):
        raise ValueError("legacy adapter result must be a JSON object")

    expected_identity = {
        "job_id": plan.get("job_id"),
        "pillar": "D",
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "task": "test",
        "source_repo": TARGET_REPOSITORY,
        "source_ref": plan.get("source_ref"),
        "target_repo": TARGET_REPOSITORY,
    }
    for key, expected in expected_identity.items():
        if legacy_result.get(key) != expected:
            raise ValueError(f"legacy adapter result {key} mismatch")

    status = legacy_result.get("status")
    if status not in ALLOWED_STATUSES:
        raise ValueError("legacy adapter result status is invalid")
    resolved_source_sha = legacy_result.get("resolved_source_sha")
    if not isinstance(resolved_source_sha, str):
        raise ValueError("legacy adapter result source SHA is invalid")
    if resolved_source_sha and not SHA40.fullmatch(resolved_source_sha):
        raise ValueError("legacy adapter result source SHA is invalid")

    test_count = legacy_result.get("test_count", 0)
    if not isinstance(test_count, int) or isinstance(test_count, bool) or test_count < 0:
        raise ValueError("legacy adapter result test count is invalid")

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "job_id": str(plan["job_id"]),
        "domain": DOMAIN,
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "adapter_sha256": adapter_bundle_sha256(),
        "token_profile": TOKEN_PROFILE,
        "source_repo": TARGET_REPOSITORY,
        "source_ref": str(plan["source_ref"]),
        "expected_source_sha": plan.get("expected_source_sha"),
        "approval_id": plan.get("approval_id"),
        "resolved_source_sha": resolved_source_sha,
        "status": status,
        "checks": bounded_checks(legacy_result),
        "artifact_references": artifact_references(
            plan, legacy_result, result_path
        ),
        "legacy_result_sha256": canonical_sha256(legacy_result),
        "test_count": test_count,
        "manifest_summary": normalize_manifest_summary(
            legacy_result.get("manifest_summary")
        ),
        "secret_scan": normalize_secret_scan(legacy_result.get("secret_scan")),
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
        "expected_source_sha": safe_optional_sha(plan, "expected_source_sha"),
        "approval_id": safe_optional_id(plan, "approval_id"),
        "resolved_source_sha": "",
        "status": "blocked",
        "checks": [],
        "artifact_references": [],
        "legacy_result_sha256": None,
        "test_count": 0,
        "manifest_summary": {},
        "secret_scan": {},
        "parent_receipt_sha256": None,
        "reason": str(reason)[:2048],
    }
    result["receipt_sha256"] = canonical_sha256(result)
    verify_canonical_result(result)
    return result


def verify_optional_sha(value: object, name: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not SHA40.fullmatch(value)
    ):
        raise ValueError(f"canonical result {name} is invalid")


def verify_optional_id(value: object, name: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not JOB_ID.fullmatch(value)
    ):
        raise ValueError(f"canonical result {name} is invalid")


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

    job_id = result.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("canonical result job_id is invalid")

    adapter_hash = result.get("adapter_sha256")
    if not isinstance(adapter_hash, str) or not SHA64.fullmatch(adapter_hash):
        raise ValueError("canonical result adapter hash is invalid")

    status = result.get("status")
    if status not in ALLOWED_STATUSES:
        raise ValueError("canonical result status is invalid")

    source_ref = result.get("source_ref")
    if source_ref != "" and not valid_source_ref(source_ref):
        raise ValueError("canonical result source_ref is invalid")
    if status != "blocked" and not valid_source_ref(source_ref):
        raise ValueError("non-blocked canonical result lacks a source_ref")

    verify_optional_sha(result.get("expected_source_sha"), "expected_source_sha")
    verify_optional_id(result.get("approval_id"), "approval_id")

    resolved_source_sha = result.get("resolved_source_sha")
    if not isinstance(resolved_source_sha, str):
        raise ValueError("canonical result source SHA is invalid")
    if resolved_source_sha and not SHA40.fullmatch(resolved_source_sha):
        raise ValueError("canonical result source SHA is invalid")

    test_count = result.get("test_count")
    if not isinstance(test_count, int) or isinstance(test_count, bool) or test_count < 0:
        raise ValueError("canonical result test_count is invalid")

    legacy_result_sha256 = result.get("legacy_result_sha256")
    if legacy_result_sha256 is not None and (
        not isinstance(legacy_result_sha256, str)
        or not SHA64.fullmatch(legacy_result_sha256)
    ):
        raise ValueError("canonical result legacy hash is invalid")
    if status != "blocked" and legacy_result_sha256 is None:
        raise ValueError("non-blocked canonical result lacks a legacy hash")

    parent_receipt = result.get("parent_receipt_sha256")
    if parent_receipt is not None and (
        not isinstance(parent_receipt, str) or not SHA64.fullmatch(parent_receipt)
    ):
        raise ValueError("canonical result parent receipt hash is invalid")

    reason = result.get("reason")
    if not isinstance(reason, str) or len(reason) > 2048:
        raise ValueError("canonical result reason is invalid")

    checks = result.get("checks")
    if not isinstance(checks, list) or len(checks) > 64:
        raise ValueError("canonical result checks are malformed")
    for check in checks:
        if not isinstance(check, dict) or set(check) != {
            "name",
            "status",
            "output_sha256",
        }:
            raise ValueError("canonical result contains a malformed check")
        name = check.get("name")
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ValueError("canonical result check name is invalid")
        if check.get("status") not in ALLOWED_STATUSES:
            raise ValueError("canonical result check status is invalid")
        output_hash = check.get("output_sha256")
        if not isinstance(output_hash, str) or not SHA64.fullmatch(output_hash):
            raise ValueError("canonical result check hash is invalid")

    artifacts = result.get("artifact_references")
    if not isinstance(artifacts, list) or len(artifacts) > 8:
        raise ValueError("canonical result artifacts are malformed")
    artifact_kinds: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"kind", "sha256"}:
            raise ValueError("canonical result contains a malformed artifact")
        kind = artifact.get("kind")
        artifact_hash = artifact.get("sha256")
        if kind not in ALLOWED_ARTIFACT_KINDS or kind in artifact_kinds:
            raise ValueError("canonical result artifact kind is invalid or duplicated")
        if not isinstance(artifact_hash, str) or not SHA64.fullmatch(artifact_hash):
            raise ValueError("canonical result artifact hash is invalid")
        artifact_kinds.add(kind)

    manifest_summary = validate_bounded_mapping(
        result.get("manifest_summary"),
        name="manifest_summary",
        allowed_keys=MANIFEST_KEYS,
    )
    for value in manifest_summary.values():
        if value is not None and (
            not isinstance(value, str) or len(value) > 128
        ):
            raise ValueError("canonical result manifest summary is invalid")

    secret_scan = validate_bounded_mapping(
        result.get("secret_scan"),
        name="secret_scan",
        allowed_keys=SECRET_SCAN_KEYS,
    )
    for key in ("files_tracked", "files_scanned", "files_skipped", "finding_count"):
        value = secret_scan.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError("canonical result secret scan count is invalid")
    scanned_commit = secret_scan.get("scanned_commit")
    if scanned_commit is not None and (
        not isinstance(scanned_commit, str) or not SHA40.fullmatch(scanned_commit)
    ):
        raise ValueError("canonical result secret scan commit is invalid")
    scan_hash = secret_scan.get("report_sha256")
    if scan_hash is not None and (
        not isinstance(scan_hash, str) or not SHA64.fullmatch(scan_hash)
    ):
        raise ValueError("canonical result secret scan hash is invalid")
    for key in ("scanner", "status"):
        value = secret_scan.get(key)
        if value is not None and (
            not isinstance(value, str) or len(value) > 128
        ):
            raise ValueError("canonical result secret scan metadata is invalid")

    if status == "completed":
        if not SHA40.fullmatch(resolved_source_sha):
            raise ValueError("completed canonical result lacks a source SHA")
        expected_source_sha = result.get("expected_source_sha")
        if expected_source_sha is not None and not hmac.compare_digest(
            expected_source_sha, resolved_source_sha
        ):
            raise ValueError("completed result violates the expected source pin")
        if not checks or any(check["status"] != "completed" for check in checks):
            raise ValueError("completed canonical result contains incomplete checks")
        if test_count < 1:
            raise ValueError("completed canonical result executed zero tests")
        if secret_scan.get("status") != "passed":
            raise ValueError("completed canonical result lacks a passing secret scan")
        if secret_scan.get("scanned_commit") != resolved_source_sha:
            raise ValueError("secret scan commit does not match resolved source")
        if secret_scan.get("files_skipped") != 0:
            raise ValueError("completed canonical result skipped secret-scan files")
        if secret_scan.get("finding_count") != 0:
            raise ValueError("completed canonical result contains secret findings")
        if not isinstance(scan_hash, str) or not SHA64.fullmatch(scan_hash):
            raise ValueError("completed canonical result lacks a scan report hash")
        scan_artifacts = [
            artifact
            for artifact in artifacts
            if artifact["kind"] == "secret-scan-report"
        ]
        if len(scan_artifacts) != 1 or scan_artifacts[0]["sha256"] != scan_hash:
            raise ValueError("secret scan artifact does not match the scan summary")


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    """Validate boundaries, preserve alias parity, and emit canonical receipts."""
    requested_action = plan.get("action") if isinstance(plan, dict) else None
    try:
        validated = validate_plan(plan)
    except ValueError as error:
        if requested_action == CANONICAL_ACTION:
            try:
                atomic_write_json(
                    result_path,
                    canonical_blocked_result(plan, str(error)),
                )
            except (OSError, ValueError):
                return 2
            return 2
        return catalog.write_result(
            blocked_plan(plan),
            result_path,
            "blocked",
            reason=str(error),
            domain=DOMAIN,
            canonical_action=CANONICAL_ACTION,
        )

    if requested_action == LEGACY_ACTION:
        return legacy.run(validated, workspace, result_path)

    try:
        result_path = prepare_result_directory(result_path)
    except (OSError, ValueError):
        return 2

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
        try:
            atomic_write_json(
                result_path,
                canonical_blocked_result(
                    validated,
                    f"canonical result conversion failed: {type(error).__name__}: {error}",
                ),
            )
        except (OSError, ValueError):
            return 2
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
