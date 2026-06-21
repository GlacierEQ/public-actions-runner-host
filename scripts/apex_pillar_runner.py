#!/usr/bin/env python3
"""Safe public execution adapter for APEX pillar jobs."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PILLARS = {
    "case-evidence": "A",
    "document-processing": "B",
    "coding-deploy": "C",
    "evolution-optimize": "D",
    "memory-sync": "E",
    "infra-gateway": "F",
    "case-ops": "G",
    "orchestrate": "H",
    "intl-case-ops": "I",
}
ALLOWED_TASKS = {
    "A": {"validate", "hash-manifest"},
    "B": {"validate", "test", "build"},
    "C": {"validate", "test", "build", "audit"},
    "D": {"validate", "test", "audit"},
    "E": {"validate", "hash-manifest"},
    "F": {"validate", "test", "audit"},
    "G": {"validate", "hash-manifest"},
    "H": {"validate", "test"},
    "I": {"validate", "hash-manifest"},
}
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
REPO = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]+$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
SKIP = {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}
CONTROL_REPO = os.environ.get("APEX_CONTROL_REPO", "GlacierEQ/llm-runner-teams")
CATALOG = Path("config/pillar-actions.json")
ADAPTER_TASK = {
    "hash-manifest": "hash-manifest",
    "validate": "validate",
    "test": "test",
    "audit": "audit",
    "document-validate": "validate",
    "latex": "validate",
    "pdf-analyze": "validate",
    "notion-sync": "validate",
    "media-queue": "validate",
    "whisperx": "validate",
    "railway": "validate",
    "xcode": "validate",
    "browser-scan": "validate",
    "health-check": "validate",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def resolve_action(payload: dict, pillar: str) -> dict | None:
    action = str(payload.get("action", ""))
    if not action:
        return None
    catalog = json.loads(CATALOG.read_text())
    matches = [item for item in catalog["actions"] if item["action"] == action and item["pillar"] == pillar]
    if len(matches) != 1:
        fail("action is not registered to the requested pillar")
    return matches[0]


def load_plan(event_path: str, manual: dict[str, str]) -> dict:
    event = json.loads(Path(event_path).read_text())
    if event.get("action") in PILLARS:
        payload = dict(event.get("client_payload") or {})
        pillar = PILLARS[event["action"]]
    else:
        payload = manual
        pillar = str(payload.get("pillar", "")).upper()

    entry = resolve_action(payload, pillar)
    plan = {
        "job_id": str(payload.get("job_id", "")),
        "pillar": pillar,
        "source_repo": entry["target_repo"] if entry else str(payload.get("source_repo") or "GlacierEQ/public-actions-runner-host"),
        "source_ref": str(payload.get("source_ref") or "main"),
        "task": ADAPTER_TASK[entry["adapter"]] if entry else str(payload.get("task") or "validate"),
        "approval_id": str(payload.get("approval_id", "")),
        "action": entry["action"] if entry else "",
        "adapter": entry["adapter"] if entry else "",
        "target_repo": entry["target_repo"] if entry else "",
    }
    if not JOB_ID.fullmatch(plan["job_id"]):
        fail("job_id must be 8-64 safe characters")
    if plan["pillar"] not in ALLOWED_TASKS:
        fail("unknown pillar")
    if plan["task"] not in ALLOWED_TASKS[plan["pillar"]]:
        fail("task is not allowed for this pillar")
    if not REPO.fullmatch(plan["source_repo"]):
        fail("source_repo must be a GlacierEQ repository")
    if not REF.fullmatch(plan["source_ref"]) or ".." in plan["source_ref"]:
        fail("invalid source_ref")
    if plan["pillar"] in {"G", "I"} and not JOB_ID.fullmatch(plan["approval_id"]):
        fail("pillars G and I require a valid private approval_id")
    return plan


def emit_outputs(plan: dict) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        print(json.dumps(plan, sort_keys=True))
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key, value in plan.items():
            handle.write(f"{key}={value}\n")
    Path(".apex-plan.json").write_text(json.dumps(plan, indent=2) + "\n")


def api(path: str, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    url = f"https://api.github.com/repos/{CONTROL_REPO}/contents/{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "apex-public-runner")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        fail(f"control-plane API request failed with status {exc.code}")


def verify_approval(job_id: str, pillar: str, approval_id: str) -> None:
    token = os.environ.get("APEX_CONTROL_TOKEN", "")
    if not token:
        fail("APEX_CONTROL_TOKEN is required")
    record = api(f"approvals/{approval_id}.json", token)
    content = json.loads(base64.b64decode(record["content"]).decode())
    if content.get("approved") is not True:
        fail("approval is not active")
    if content.get("job_id") != job_id or content.get("pillar") != pillar:
        fail("approval does not match this job")
    print("Private dual-confirmation record verified.")


def files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in SKIP for part in path.parts):
            yield path


def command_for(task: str, root: Path) -> list[str] | None:
    if task == "test":
        if (root / "package.json").exists():
            return ["npm", "test", "--", "--runInBand"]
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
            return [sys.executable, "-m", "pytest", "-q"]
    if task == "build" and (root / "package.json").exists():
        return ["npm", "run", "build"]
    if task == "audit" and (root / "package.json").exists():
        return ["npm", "audit", "--json"]
    return None


def execute(plan: dict, root: Path, result_path: Path) -> int:
    result = {
        "schema_version": "1.0",
        "job_id": plan["job_id"],
        "pillar": plan["pillar"],
        "task": plan["task"],
        "source_repo": plan["source_repo"],
        "source_ref": plan["source_ref"],
        "status": "completed",
    }
    exit_code = 0
    if plan["task"] == "hash-manifest":
        manifest = []
        for path in files(root):
            rel = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.append({"path": rel, "sha256": digest, "bytes": path.stat().st_size})
        result["manifest"] = manifest
        result["file_count"] = len(manifest)
    elif plan["task"] == "validate":
        invalid_json = []
        count = 0
        for path in files(root):
            count += 1
            if path.suffix == ".json" and path.stat().st_size < 5_000_000:
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    invalid_json.append(path.relative_to(root).as_posix())
        result["file_count"] = count
        result["invalid_json"] = invalid_json
        if invalid_json:
            result["status"] = "failed"
            exit_code = 1
    else:
        command = command_for(plan["task"], root)
        if command is None:
            result["status"] = "skipped"
            result["reason"] = "No allowlisted command applies to this repository"
            exit_code = 2
        else:
            proc = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=1800)
            combined = (proc.stdout + "\n" + proc.stderr)[-100_000:]
            result["command"] = command
            result["exit_code"] = proc.returncode
            result["output_sha256"] = hashlib.sha256(combined.encode()).hexdigest()
            result["output_tail"] = combined[-32_000:]
            if proc.returncode:
                result["status"] = "failed"
                exit_code = proc.returncode
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Job {plan['job_id']} finished with status {result['status']}.")
    return exit_code


def publish(job_id: str, result_path: Path) -> None:
    token = os.environ.get("APEX_CONTROL_TOKEN", "")
    if not token:
        fail("APEX_CONTROL_TOKEN is required")
    path = f"results/{job_id}.json"
    content = base64.b64encode(result_path.read_bytes()).decode()
    payload = {"message": f"runner: record result {job_id}", "content": content}
    try:
        existing = api(path, token)
        payload["sha"] = existing["sha"]
    except SystemExit:
        pass
    api(path, token, method="PUT", payload=payload)
    print("Result published to the private control plane.")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--event", required=True)
    for name in ("pillar", "job-id", "source-repo", "source-ref", "task", "approval-id", "action"):
        plan_cmd.add_argument(f"--{name}", default="")

    verify = sub.add_parser("verify-approval")
    verify.add_argument("--job-id", required=True)
    verify.add_argument("--pillar", required=True)
    verify.add_argument("--approval-id", required=True)

    run = sub.add_parser("run")
    run.add_argument("--plan", default=".apex-plan.json")
    run.add_argument("--workspace", required=True)
    run.add_argument("--result", required=True)

    pub = sub.add_parser("publish")
    pub.add_argument("--job-id", required=True)
    pub.add_argument("--result", required=True)

    args = parser.parse_args()
    if args.command == "plan":
        manual = {
            "pillar": args.pillar,
            "job_id": args.job_id,
            "source_repo": args.source_repo,
            "source_ref": args.source_ref,
            "task": args.task,
            "approval_id": args.approval_id,
            "action": args.action,
        }
        plan = load_plan(args.event, manual)
        emit_outputs(plan)
        return 0
    if args.command == "verify-approval":
        verify_approval(args.job_id, args.pillar, args.approval_id)
        return 0
    if args.command == "run":
        return execute(json.loads(Path(args.plan).read_text()), Path(args.workspace), Path(args.result))
    publish(args.job_id, Path(args.result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
