#!/usr/bin/env python3
"""Execute a cataloged pillar action through a safe public adapter."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for import_path in (str(ROOT), str(SCRIPT_DIR)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

import apex_pillar_runner as base
from workload_isolation import (
    CheckoutHandle,
    DIRECTORY_FLAGS,
    FILE_FLAGS,
    WorkloadIsolationError,
    open_checkout,
)

BASE_TASKS = {
    "hash-manifest": "hash-manifest",
    "validate": "validate",
    "test": "test",
    "audit": "audit",
}
MEDIA_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}
OFFICE_SUFFIXES = {".docx", ".odt", ".ods", ".odp", ".pptx", ".xlsx"}
MAX_EMBEDDED_RECORDS = 256
MAX_INVENTORY_ENTRIES = 100_000
MAX_INVENTORY_BYTES = 10_000_000_000
HASH_CHUNK_BYTES = 1024 * 1024


class InventoryBoundaryError(RuntimeError):
    """Raised when a no-follow inventory cannot be completed safely."""


def write_result(plan: dict, result_path: Path, status: str, **details) -> int:
    result = {
        "schema_version": "1.1",
        "job_id": plan["job_id"],
        "pillar": plan["pillar"],
        "action": plan.get("action"),
        "adapter": plan.get("adapter"),
        "task": plan.get("task"),
        "source_repo": plan.get("source_repo"),
        "source_ref": plan.get("source_ref"),
        "resolved_source_sha": os.environ.get("APEX_RESOLVED_SOURCE_SHA", ""),
        "target_repo": plan.get("target_repo"),
        "provenance": base.provenance(plan),
        "status": status,
        **details,
    }
    result_path = result_path.resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Action {plan.get('action') or plan.get('task')} finished with status {status}.")
    return 0 if status == "completed" else 2


def bounded_process(
    command: list[str],
    cwd: Path,
    timeout: int,
    *,
    pass_fds: tuple[int, ...] = (),
) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            shell=False,
            pass_fds=pass_fds,
        )
        return proc.returncode, (proc.stdout or "")[-32_000:], ""
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return None, output[-32_000:], f"timeout after {timeout} seconds"
    except OSError as exc:
        return None, "", f"process start failed: {type(exc).__name__}: {exc}"


def canonical_record_bytes(record: dict) -> bytes:
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _same_regular_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat.S_ISREG(before.st_mode)
        and stat.S_ISREG(after.st_mode)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _stream_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, HASH_CHUNK_BYTES, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def _inspect_regular_file(
    directory_fd: int,
    name: str,
    relative: PurePosixPath,
    metadata: os.stat_result,
    inspector: Callable[[int, os.stat_result, str], dict],
) -> dict:
    descriptor: int | None = None
    try:
        descriptor = os.open(name, FILE_FLAGS, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        if not _same_regular_file(metadata, opened):
            raise InventoryBoundaryError(
                "inventory file identity changed before no-follow open completed"
            )
        record = inspector(descriptor, opened, relative.as_posix())
        after = os.fstat(descriptor)
        if not _same_regular_file(opened, after):
            raise InventoryBoundaryError("inventory file changed while being read")
        return record
    except OSError as error:
        raise InventoryBoundaryError(
            f"inventory file open/read failed without following symlinks: "
            f"{type(error).__name__}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _walk_inventory(
    checkout: CheckoutHandle,
    suffixes: set[str],
    inspector: Callable[[int, os.stat_result, str], dict],
) -> tuple[list[dict], int, int]:
    records: list[dict] = []
    symlink_entries = 0
    total_bytes = 0
    entry_count = 0

    def visit(directory_fd: int, relative_directory: PurePosixPath) -> None:
        nonlocal symlink_entries, total_bytes, entry_count
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise InventoryBoundaryError(
                f"inventory directory listing failed: {type(error).__name__}"
            ) from error

        for name in names:
            if name in base.SKIP:
                continue
            entry_count += 1
            if entry_count > MAX_INVENTORY_ENTRIES:
                raise InventoryBoundaryError("inventory entry ceiling exceeded")
            relative = relative_directory / name
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise InventoryBoundaryError(
                    f"inventory metadata read failed: {type(error).__name__}"
                ) from error

            if stat.S_ISLNK(metadata.st_mode):
                symlink_entries += 1
                continue
            if stat.S_ISDIR(metadata.st_mode):
                child_fd: int | None = None
                try:
                    child_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=directory_fd)
                    opened = os.fstat(child_fd)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or opened.st_dev != metadata.st_dev
                        or opened.st_ino != metadata.st_ino
                    ):
                        raise InventoryBoundaryError(
                            "inventory directory identity changed during traversal"
                        )
                    visit(child_fd, relative)
                except OSError as error:
                    raise InventoryBoundaryError(
                        f"inventory directory open failed without following symlinks: "
                        f"{type(error).__name__}"
                    ) from error
                finally:
                    if child_fd is not None:
                        os.close(child_fd)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if Path(name).suffix.lower() not in suffixes:
                continue

            total_bytes += metadata.st_size
            if total_bytes > MAX_INVENTORY_BYTES:
                raise InventoryBoundaryError("inventory byte ceiling exceeded")
            records.append(
                _inspect_regular_file(
                    directory_fd,
                    name,
                    relative,
                    metadata,
                    inspector,
                )
            )

    checkout.assert_path_identity()
    root_fd = os.dup(checkout.fd)
    try:
        visit(root_fd, PurePosixPath())
    finally:
        os.close(root_fd)
    checkout.assert_path_identity()
    return records, symlink_entries, total_bytes


def _inventory_result(
    plan: dict,
    result_path: Path,
    *,
    records: list[dict],
    symlink_entries: int,
    total_bytes: int,
    noun: str,
    empty_reason: str,
    invalid_field: str | None = None,
) -> int:
    manifest = hashlib.sha256()
    invalid: list[str] = []
    invalid_count = 0
    for record in records:
        manifest.update(canonical_record_bytes(record))
        manifest.update(b"\n")
        if invalid_field and not record[invalid_field]:
            invalid_count += 1
            if len(invalid) < MAX_EMBEDDED_RECORDS:
                invalid.append(record["path"])

    status = "completed"
    reason = ""
    if not records:
        status = "blocked"
        reason = empty_reason
    elif symlink_entries:
        status = "failed"
        reason = "inventory contains symlink entries; no symlink target was read"
    elif invalid_count:
        status = "failed"

    embedded = records[:MAX_EMBEDDED_RECORDS]
    details = {
        f"{noun}_count": len(records),
        f"{noun}_total_bytes": total_bytes,
        f"{noun}_records_included": len(embedded),
        f"{noun}_records_truncated": len(records) > len(embedded),
        f"{noun}_manifest_sha256": manifest.hexdigest(),
        "symlink_entries_rejected": symlink_entries,
        "inventory_complete": symlink_entries == 0,
        noun: embedded,
        "reason": reason,
    }
    if invalid_field:
        details.update(
            {
                f"invalid_{noun}_count": invalid_count,
                f"invalid_{noun}": invalid,
                f"invalid_{noun}_truncated": invalid_count > len(invalid),
            }
        )
    return write_result(plan, result_path, status, **details)


def media_queue(plan: dict, workspace: Path, result_path: Path) -> int:
    def inspect(descriptor: int, metadata: os.stat_result, relative: str) -> dict:
        return {
            "path": relative,
            "bytes": metadata.st_size,
            "sha256": _stream_sha256(descriptor),
        }

    try:
        with open_checkout(workspace, label="media workload") as checkout:
            records, symlinks, total_bytes = _walk_inventory(
                checkout,
                MEDIA_SUFFIXES,
                inspect,
            )
    except (InventoryBoundaryError, WorkloadIsolationError) as error:
        return write_result(
            plan,
            result_path,
            "failed",
            reason=f"media inventory boundary failed: {error}",
        )
    return _inventory_result(
        plan,
        result_path,
        records=records,
        symlink_entries=symlinks,
        total_bytes=total_bytes,
        noun="media",
        empty_reason="No media files found",
    )


def pdf_analyze(plan: dict, workspace: Path, result_path: Path) -> int:
    def inspect(descriptor: int, metadata: os.stat_result, relative: str) -> dict:
        header = os.pread(descriptor, 8, 0)
        return {
            "path": relative,
            "bytes": metadata.st_size,
            "sha256": _stream_sha256(descriptor),
            "valid_header": header.startswith(b"%PDF-"),
        }

    try:
        with open_checkout(workspace, label="PDF workload") as checkout:
            records, symlinks, total_bytes = _walk_inventory(
                checkout,
                {".pdf"},
                inspect,
            )
    except (InventoryBoundaryError, WorkloadIsolationError) as error:
        return write_result(
            plan,
            result_path,
            "failed",
            reason=f"PDF inventory boundary failed: {error}",
        )
    return _inventory_result(
        plan,
        result_path,
        records=records,
        symlink_entries=symlinks,
        total_bytes=total_bytes,
        noun="pdf",
        empty_reason="No PDF files found",
        invalid_field="valid_header",
    )


def document_validate(plan: dict, workspace: Path, result_path: Path) -> int:
    def inspect(descriptor: int, metadata: os.stat_result, relative: str) -> dict:
        duplicate = os.dup(descriptor)
        try:
            with os.fdopen(duplicate, "rb") as handle:
                valid = zipfile.is_zipfile(handle)
        except Exception as error:
            raise InventoryBoundaryError(
                f"office container validation failed: {type(error).__name__}"
            ) from error
        return {
            "path": relative,
            "bytes": metadata.st_size,
            "sha256": _stream_sha256(descriptor),
            "valid_container": valid,
        }

    try:
        with open_checkout(workspace, label="document workload") as checkout:
            records, symlinks, total_bytes = _walk_inventory(
                checkout,
                OFFICE_SUFFIXES,
                inspect,
            )
    except (InventoryBoundaryError, WorkloadIsolationError) as error:
        return write_result(
            plan,
            result_path,
            "failed",
            reason=f"document inventory boundary failed: {error}",
        )
    return _inventory_result(
        plan,
        result_path,
        records=records,
        symlink_entries=symlinks,
        total_bytes=total_bytes,
        noun="document",
        empty_reason="No supported office documents found",
        invalid_field="valid_container",
    )


def latex_compile(plan: dict, workspace: Path, result_path: Path) -> int:
    engine = shutil.which("tectonic") or shutil.which("latexmk")
    try:
        checkout = open_checkout(workspace, label="LaTeX workload")
    except WorkloadIsolationError as error:
        return write_result(plan, result_path, "failed", reason=str(error))
    with checkout:
        sources = sorted(checkout.proc_path.rglob("*.tex"))
        if not sources:
            return write_result(plan, result_path, "blocked", reason="No TeX source found")
        if not engine:
            return write_result(
                plan,
                result_path,
                "blocked",
                reason="Tectonic or latexmk runtime is not installed",
            )
        source = sources[0]
        command = (
            [engine, source.name]
            if Path(engine).name == "tectonic"
            else [engine, "-pdf", "-interaction=nonstopmode", source.name]
        )
        exit_code, output, error = bounded_process(
            command,
            source.parent,
            1800,
            pass_fds=checkout.pass_fds,
        )
        checkout.assert_path_identity()
        status = "completed" if exit_code == 0 and not error else "failed"
        return write_result(
            plan,
            result_path,
            status,
            source=source.relative_to(checkout.proc_path).as_posix(),
            exit_code=exit_code,
            reason=error,
            output_sha256=hashlib.sha256(output.encode()).hexdigest(),
            output_tail=output,
        )


def xcode_validate(plan: dict, workspace: Path, result_path: Path) -> int:
    xcodebuild = shutil.which("xcodebuild")
    try:
        checkout = open_checkout(workspace, label="Xcode workload")
    except WorkloadIsolationError as error:
        return write_result(plan, result_path, "failed", reason=str(error))
    with checkout:
        projects = sorted(checkout.proc_path.rglob("*.xcodeproj"))
        workspaces = sorted(checkout.proc_path.rglob("*.xcworkspace"))
        if not xcodebuild:
            return write_result(
                plan,
                result_path,
                "blocked",
                reason="xcodebuild requires a public macOS runner",
            )
        target = workspaces[0] if workspaces else projects[0] if projects else None
        if target is None:
            return write_result(
                plan,
                result_path,
                "blocked",
                reason="No Xcode project or workspace found",
            )
        flag = "-workspace" if target.suffix == ".xcworkspace" else "-project"
        command = [xcodebuild, flag, str(target), "-list"]
        exit_code, output, error = bounded_process(
            command,
            checkout.proc_path,
            900,
            pass_fds=checkout.pass_fds,
        )
        checkout.assert_path_identity()
        status = "completed" if exit_code == 0 and not error else "failed"
        return write_result(
            plan,
            result_path,
            status,
            target=target.relative_to(checkout.proc_path).as_posix(),
            exit_code=exit_code,
            reason=error,
            output_sha256=hashlib.sha256(output.encode()).hexdigest(),
            output_tail=output,
        )


def run_registered_specialization(
    plan: dict, workspace: Path, result_path: Path
) -> int | None:
    action = plan.get("action")
    if action == "code.monolith.validate-atlases":
        from domains.code.adapters.monolith_atlas_validate import run

        return run(plan, workspace, result_path)
    if action == "code.monolith.validate-legal-live-reconciliation":
        from domains.code.adapters.monolith_legal_live_validate import run

        return run(plan, workspace, result_path)
    if action == "code.monolith.validate-company-engineered-registry":
        from domains.code.adapters.monolith_company_registry_validate import run

        return run(plan, workspace, result_path)
    if action == "code.casey-legal-mcp.validate-v2":
        from domains.code.adapters.casey_legal_mcp_validate import run

        return run(plan, workspace, result_path)
    if action == "code.fileboss.validate-operator-code-bridge":
        from domains.code.adapters.fileboss_operator_code_validate import run

        return run(plan, workspace, result_path)
    if action == "mega-pdf-function-genome":
        from domains.code.adapters.mega_pdf_function_genome import run

        return run(plan, workspace, result_path)
    if action == "docs.monolith.validate-integrity":
        from domains.docs.adapters.monolith_docs_validate import run

        return run(plan, workspace, result_path)
    if action == "analysis.monolith.estate-health":
        from domains.analysis.adapters.monolith_estate_health import run

        return run(plan, workspace, result_path)
    return None


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: apex_catalog_runner.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    adapter = plan.get("adapter")
    if not adapter:
        return base.execute(plan, workspace, result_path)

    specialized = run_registered_specialization(plan, workspace, result_path)
    if specialized is not None:
        return specialized

    task = BASE_TASKS.get(adapter)
    if task:
        executable = dict(plan)
        executable["task"] = task
        return base.execute(executable, workspace, result_path)
    if adapter == "document-validate":
        return document_validate(plan, workspace, result_path)
    if adapter == "media-queue":
        return media_queue(plan, workspace, result_path)
    if adapter == "pdf-analyze":
        return pdf_analyze(plan, workspace, result_path)
    if adapter == "latex":
        return latex_compile(plan, workspace, result_path)
    if adapter == "xcode":
        return xcode_validate(plan, workspace, result_path)
    if adapter == "browser-scan":
        executable = dict(plan)
        executable["task"] = "test"
        return base.execute(executable, workspace, result_path)
    if adapter == "health-check":
        executable = dict(plan)
        executable["task"] = "validate"
        return base.execute(executable, workspace, result_path)

    requirements = {
        "notion-sync": "NOTION_TOKEN and a structured page/database payload",
        "whisperx": "WhisperX model runtime and a private media artifact reference",
        "railway": "RAILWAY_TOKEN, Railway CLI, and deployment approval",
    }
    return write_result(
        plan,
        result_path,
        "blocked",
        reason=f"Adapter requires {requirements.get(adapter, 'a dedicated runtime contract')}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
