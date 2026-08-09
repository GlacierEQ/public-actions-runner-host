"""Recover the MEGA-PDF Function Genome through the canonical read-only APEX face."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import apex_catalog_runner as catalog

from scripts.workload_isolation import (
    WorkloadIsolationError,
    attest_checkout,
    build_environment,
    command_contract_sha256,
    open_checkout,
)

EXPECTED_ACTION = "mega-pdf-function-genome"
EXPECTED_REPOSITORY = "GlacierEQ/MEGA-PDF"
EXPECTED_ADAPTER = "mega-pdf-function-genome"
SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_SCHEMA = "mega-pdf-function-genome/v3"
REQUIRED_PATHS = (
    "mega_pdf_control_plane/__init__.py",
    "mega_pdf_control_plane/function_genome.py",
    "mega_pdf_control_plane/genome_common.py",
    "mega_pdf_control_plane/genome_javascript.py",
    "mega_pdf_control_plane/genome_python.py",
    "mega_pdf_control_plane/models.py",
    "tests/test_mega_pdf_control_plane.py",
    "tests/test_function_genome_ingestion.py",
)
OUTPUTS = (
    "artifacts/function-genome.json",
    "artifacts/probe-receipts.jsonl",
    "artifacts/function-genome-summary.md",
    "artifacts/relay-receipt.json",
)
MAX_OUTPUT_FILE_BYTES = 2_500_000
MAX_OUTPUT_TOTAL_BYTES = 3_500_000
MAX_EMBEDDED_PAYLOAD_BYTES = 3_500_000


class FunctionGenomeError(RuntimeError):
    """Raised when a bounded Function Genome proof cannot be produced safely."""


def validate_plan(plan: dict) -> None:
    expected = {
        "pillar": "C",
        "action": EXPECTED_ACTION,
        "adapter": EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": EXPECTED_REPOSITORY,
        "target_repo": EXPECTED_REPOSITORY,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise ValueError(f"{field} identity mismatch")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _verify_receipt(row: object, previous_hash: str | None) -> str:
    if not isinstance(row, dict):
        raise FunctionGenomeError("probe receipt row is not an object")
    payload = dict(row)
    expected_hash = payload.pop("receipt_hash", None)
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ):
        raise FunctionGenomeError("probe receipt hash is unavailable or invalid")
    if payload.get("previous_receipt_hash") != previous_hash:
        raise FunctionGenomeError("probe receipt chain is discontinuous")
    actual_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    if actual_hash != expected_hash:
        raise FunctionGenomeError("probe receipt hash verification failed")
    return expected_hash


def verify_generated_outputs(
    output_root: Path, resolved_sha: str
) -> dict[str, object]:
    genome_path = output_root / "artifacts/function-genome.json"
    receipts_path = output_root / "artifacts/probe-receipts.jsonl"
    summary_path = output_root / "artifacts/function-genome-summary.md"
    for path in (genome_path, receipts_path, summary_path):
        if path.is_symlink() or not path.is_file():
            raise FunctionGenomeError(
                f"generator did not create regular output: {path.name}"
            )

    try:
        report = json.loads(genome_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FunctionGenomeError("Function Genome report is invalid JSON") from error
    if not isinstance(report, dict) or report.get("schema_version") != EXPECTED_SCHEMA:
        raise FunctionGenomeError(
            "Function Genome schema does not match the recovery contract"
        )
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise FunctionGenomeError("Function Genome summary is unavailable")

    discovered = int(summary.get("discovered", -1))
    promoted = int(summary.get("promoted_to_probed", -1))
    blocked = int(summary.get("blocked", -1))
    receipt_count = int(summary.get("receipts", -1))
    if discovered < 0 or promoted < 0 or blocked < 0 or receipt_count < 0:
        raise FunctionGenomeError("Function Genome summary contains invalid counts")
    if discovered != promoted + blocked:
        raise FunctionGenomeError("Function Genome lifecycle counts do not reconcile")
    if receipt_count != discovered:
        raise FunctionGenomeError(
            "Function Genome receipt count does not match discovery count"
        )
    if int(summary.get("approved", -1)) != 0:
        raise FunctionGenomeError(
            "static recovery attempted to promote APPROVED functions"
        )
    if int(summary.get("defaults_promoted", -1)) != 0:
        raise FunctionGenomeError(
            "static recovery attempted to promote DEFAULT functions"
        )
    if report.get("receipt_chain_valid") is not True:
        raise FunctionGenomeError(
            "Function Genome did not assert a valid receipt chain"
        )

    previous_hash: str | None = None
    rows: list[dict] = []
    try:
        for raw in receipts_path.read_text(encoding="utf-8").splitlines():
            if not raw:
                continue
            row = json.loads(raw)
            previous_hash = _verify_receipt(row, previous_hash)
            rows.append(row)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FunctionGenomeError("probe receipt stream is invalid") from error
    if len(rows) != receipt_count:
        raise FunctionGenomeError(
            "verified receipt rows do not match the declared count"
        )
    receipt_root = report.get("receipt_root")
    if rows:
        if previous_hash != receipt_root:
            raise FunctionGenomeError("verified receipt root does not match the report")
    elif discovered != 0 or not isinstance(receipt_root, str) or not re.fullmatch(
        r"[0-9a-f]{64}", receipt_root
    ):
        raise FunctionGenomeError("empty receipt population has an invalid root")
    if not isinstance(report.get("inventory_digest"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", str(report.get("inventory_digest"))
    ):
        raise FunctionGenomeError("inventory digest is unavailable or invalid")
    if not summary_path.read_text(encoding="utf-8").strip():
        raise FunctionGenomeError("Function Genome markdown summary is empty")

    relay_receipt = {
        "schema_version": "mega-pdf-canonical-apex-recovery/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "credential_path": "canonical_apex_oidc_read_only",
        "source_repo": EXPECTED_REPOSITORY,
        "source_sha": resolved_sha,
        "inventory_digest": report["inventory_digest"],
        "receipt_root": report["receipt_root"],
        "receipt_chain_valid": True,
        "discovered": discovered,
        "promoted_to_probed": promoted,
        "blocked": blocked,
        "approved": 0,
        "defaults_promoted": 0,
        "parse_failures": len(report.get("parse_failures", [])),
        "scan_issues": len(report.get("scan_issues", [])),
    }
    relay_path = output_root / "artifacts/relay-receipt.json"
    relay_path.write_text(
        json.dumps(relay_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"report": report, "relay_receipt": relay_receipt}


def collect_payload(output_root: Path, resolved_sha: str) -> dict[str, object]:
    files: list[dict[str, object]] = []
    total_bytes = 0
    for relative in OUTPUTS:
        path = output_root / relative
        if path.is_symlink() or not path.is_file():
            raise FunctionGenomeError(
                f"expected recovery artifact is unavailable: {relative}"
            )
        payload = path.read_bytes()
        if len(payload) > MAX_OUTPUT_FILE_BYTES:
            raise FunctionGenomeError(
                f"recovery artifact exceeds per-file bound: {relative}"
            )
        total_bytes += len(payload)
        if total_bytes > MAX_OUTPUT_TOTAL_BYTES:
            raise FunctionGenomeError(
                "recovery artifact payload exceeds total byte bound"
            )
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FunctionGenomeError(
                f"recovery artifact is not UTF-8: {relative}"
            ) from error
        files.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content": content,
            }
        )

    embedded_bytes = len(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    if embedded_bytes > MAX_EMBEDDED_PAYLOAD_BYTES:
        raise FunctionGenomeError(
            "embedded recovery artifact payload exceeds result byte bound"
        )
    return {
        "status": "available",
        "resolved_source_sha": resolved_sha,
        "total_bytes": total_bytes,
        "embedded_bytes": embedded_bytes,
        "files": files,
    }


def commands(
    result_path: Path, job_id: str, workspace_root: Path, output_root: Path
) -> list[list[str]]:
    venv = result_path.resolve().parent / f"venv-{job_id}"
    python = venv / "bin" / "python"
    module_launcher = (
        "import runpy,sys; root=sys.argv.pop(1); sys.path.insert(0,root); "
        "runpy.run_module('mega_pdf_control_plane.function_genome',run_name='__main__')"
    )
    return [
        [sys.executable, "-m", "venv", str(venv)],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "pytest==8.4.1",
        ],
        [
            str(python),
            "-m",
            "py_compile",
            "mega_pdf_control_plane/function_genome.py",
            "mega_pdf_control_plane/genome_common.py",
            "mega_pdf_control_plane/genome_javascript.py",
            "mega_pdf_control_plane/genome_python.py",
            "mega_pdf_control_plane/models.py",
            "tests/test_mega_pdf_control_plane.py",
            "tests/test_function_genome_ingestion.py",
        ],
        [
            str(python),
            "-m",
            "pytest",
            "-q",
            "tests/test_mega_pdf_control_plane.py",
            "tests/test_function_genome_ingestion.py",
        ],
        [
            str(python),
            "-c",
            module_launcher,
            str(workspace_root),
            "--root",
            str(workspace_root),
            "--output",
            str(output_root / "artifacts/function-genome.json"),
            "--receipts",
            str(output_root / "artifacts/probe-receipts.jsonl"),
            "--summary",
            str(output_root / "artifacts/function-genome-summary.md"),
        ],
    ]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    result_path = result_path.resolve()
    try:
        validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA is unavailable or invalid",
        )

    output_root = result_path.parent / f"mega-pdf-recovery-{plan['job_id']}"
    if output_root.exists():
        shutil.rmtree(output_root)
    (output_root / "artifacts").mkdir(parents=True, mode=0o700)

    try:
        checkout = open_checkout(workspace, label="MEGA-PDF workload")
        env = build_environment(
            result_path,
            str(plan["job_id"]),
            extra={"APEX_RESOLVED_SOURCE_SHA": resolved_sha},
        )
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"workload isolation failed before execution: {error}",
        )

    steps: list[dict[str, object]] = []
    status = "completed"
    payload: dict[str, object] | None = None
    verification: dict[str, object] | None = None
    post_attestation: dict[str, object] | None = None
    command_contract = ""

    with checkout:
        try:
            pre_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            return catalog.write_result(
                plan,
                result_path,
                "blocked",
                reason=f"workload isolation failed before execution: {error}",
            )

        workspace_root = checkout.proc_path
        missing = [
            relative
            for relative in REQUIRED_PATHS
            if not (workspace_root / relative).is_file()
        ]
        if missing:
            return catalog.write_result(
                plan,
                result_path,
                "blocked",
                reason="required MEGA-PDF recovery files are missing: "
                + ", ".join(missing),
            )

        sequence = commands(
            result_path, str(plan["job_id"]), workspace_root, output_root
        )
        command_contract = command_contract_sha256(
            sequence,
            volatile_roots=(result_path.parent, workspace_root),
        )
        for command in sequence:
            try:
                process = subprocess.run(
                    command,
                    cwd=workspace_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=1800,
                    check=False,
                    shell=False,
                    env=env,
                    pass_fds=checkout.pass_fds,
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
                    status = "failed"
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
                status = "failed"
                break
            except OSError as error:
                steps.append(
                    {
                        "command": command,
                        "status": "failed",
                        "reason": (
                            f"process start failed: {type(error).__name__}: {error}"
                        ),
                    }
                )
                status = "failed"
                break

        if status == "completed":
            try:
                verification = verify_generated_outputs(output_root, resolved_sha)
                payload = collect_payload(output_root, resolved_sha)
            except (FunctionGenomeError, OSError, UnicodeDecodeError) as error:
                status = "failed"
                steps.append(
                    {
                        "command": ["verify-function-genome-artifacts"],
                        "status": "failed",
                        "reason": str(error),
                    }
                )

        try:
            post_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            status = "failed"
            steps.append(
                {
                    "command": ["workload-attestation"],
                    "status": "failed",
                    "reason": str(error),
                }
            )

    details: dict[str, object] = {
        "steps": steps,
        "command_contract_sha256": command_contract,
        "workspace_attestation": {
            "before": pre_attestation,
            "after": post_attestation,
        },
    }
    if verification is not None:
        report = verification["report"]
        details["function_genome_summary"] = report["summary"]
        details["inventory_digest"] = report["inventory_digest"]
        details["receipt_root"] = report["receipt_root"]
    if payload is not None:
        details["recovery_artifacts"] = payload

    return catalog.write_result(plan, result_path, status, **details)
