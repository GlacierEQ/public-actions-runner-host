#!/usr/bin/env python3
"""Execute a cataloged pillar action through a safe public adapter."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import apex_pillar_runner as base

BASE_TASKS = {
    "hash-manifest": "hash-manifest",
    "validate": "validate",
    "test": "test",
    "audit": "audit",
}
MEDIA_SUFFIXES = {".aac", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
OFFICE_SUFFIXES = {".docx", ".odt", ".ods", ".odp", ".pptx", ".xlsx"}


def write_result(plan: dict, result_path: Path, status: str, **details) -> int:
    result = {
        "schema_version": "1.0",
        "job_id": plan["job_id"],
        "pillar": plan["pillar"],
        "action": plan.get("action"),
        "adapter": plan.get("adapter"),
        "status": status,
        **details,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Action {plan.get('action') or plan.get('task')} finished with status {status}.")
    return 0 if status == "completed" else 2


def media_queue(plan: dict, workspace: Path, result_path: Path) -> int:
    items = []
    for path in base.files(workspace):
        if path.suffix.lower() in MEDIA_SUFFIXES:
            items.append({
                "path": path.relative_to(workspace).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    return write_result(plan, result_path, "completed", media_count=len(items), media=items)


def pdf_analyze(plan: dict, workspace: Path, result_path: Path) -> int:
    documents = []
    invalid = []
    for path in base.files(workspace):
        if path.suffix.lower() != ".pdf":
            continue
        header = path.read_bytes()[:8]
        item = {
            "path": path.relative_to(workspace).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "valid_header": header.startswith(b"%PDF-"),
        }
        documents.append(item)
        if not item["valid_header"]:
            invalid.append(item["path"])
    status = "completed" if documents and not invalid else "failed"
    return write_result(
        plan,
        result_path,
        status,
        pdf_count=len(documents),
        invalid_pdf_headers=invalid,
        documents=documents,
        reason="No PDF files found" if not documents else "",
    )


def document_validate(plan: dict, workspace: Path, result_path: Path) -> int:
    documents = []
    invalid = []
    for path in base.files(workspace):
        suffix = path.suffix.lower()
        if suffix not in OFFICE_SUFFIXES:
            continue
        valid = zipfile.is_zipfile(path)
        item = {
            "path": path.relative_to(workspace).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "valid_container": valid,
        }
        documents.append(item)
        if not valid:
            invalid.append(item["path"])
    status = "completed" if not invalid else "failed"
    return write_result(
        plan,
        result_path,
        status,
        document_count=len(documents),
        invalid_containers=invalid,
        documents=documents,
    )


def latex_compile(plan: dict, workspace: Path, result_path: Path) -> int:
    engine = shutil.which("tectonic") or shutil.which("latexmk")
    sources = sorted(workspace.rglob("*.tex"))
    if not sources:
        return write_result(plan, result_path, "blocked", reason="No TeX source found")
    if not engine:
        return write_result(plan, result_path, "blocked", reason="Tectonic or latexmk runtime is not installed")
    source = sources[0]
    if Path(engine).name == "tectonic":
        command = [engine, source.name]
    else:
        command = [engine, "-pdf", "-interaction=nonstopmode", source.name]
    proc = subprocess.run(command, cwd=source.parent, text=True, capture_output=True, timeout=1800)
    output = (proc.stdout + "\n" + proc.stderr)[-32000:]
    return write_result(
        plan,
        result_path,
        "completed" if proc.returncode == 0 else "failed",
        source=source.relative_to(workspace).as_posix(),
        exit_code=proc.returncode,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        output_tail=output,
    )


def xcode_validate(plan: dict, workspace: Path, result_path: Path) -> int:
    xcodebuild = shutil.which("xcodebuild")
    projects = sorted(workspace.rglob("*.xcodeproj"))
    workspaces = sorted(workspace.rglob("*.xcworkspace"))
    if not xcodebuild:
        return write_result(plan, result_path, "blocked", reason="xcodebuild requires a public macOS runner")
    target = workspaces[0] if workspaces else projects[0] if projects else None
    if target is None:
        return write_result(plan, result_path, "blocked", reason="No Xcode project or workspace found")
    flag = "-workspace" if target.suffix == ".xcworkspace" else "-project"
    proc = subprocess.run([xcodebuild, flag, str(target), "-list"], cwd=workspace, text=True, capture_output=True, timeout=900)
    output = (proc.stdout + "\n" + proc.stderr)[-32000:]
    return write_result(
        plan,
        result_path,
        "completed" if proc.returncode == 0 else "failed",
        target=target.relative_to(workspace).as_posix(),
        exit_code=proc.returncode,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        output_tail=output,
    )


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: apex_catalog_runner.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text())
    adapter = plan.get("adapter")
    if not adapter:
        return base.execute(plan, workspace, result_path)

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
