#!/usr/bin/env python3
"""Safe public execution adapter and immutable private claim/receipt bridge."""
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
from datetime import datetime, timezone
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
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
SKIP = {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}
CONTROL_REPO = os.environ.get("APEX_CONTROL_REPO", "GlacierEQ/llm-runner-teams")
CATALOG = Path("config/pillar-actions.json")
MAX_RESULT_BYTES = 5_000_000
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
PROVENANCE_FIELDS = {
    "workflow_run_id",
    "workflow_run_attempt",
    "trigger_actor",
    "trigger_actor_id",
    "event_name",
    "execution_repo",
    "public_runner_sha",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def resolve_action(payload: dict, pillar: str) -> dict | None:
    action = str(payload.get("action", ""))
    if not action:
        return None
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    matches = [item for item in catalog["actions"] if item["action"] == action and item["pillar"] == pillar]
    if len(matches) != 1:
        fail("action is not registered to the requested pillar")
    return matches[0]


def load_plan(event_path: str, manual: dict[str, str]) -> dict:
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
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
            text = str(value)
            if "\n" in text or "\r" in text:
                fail(f"plan output {key} contains a newline")
            handle.write(f"{key}={text}\n")
    Path(".apex-plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def api(
    path: str,
    token: str,
    method: str = "GET",
    payload: dict | None = None,
    *,
    allow_not_found: bool = False,
) -> dict | None:
    url = f"https://api.github.com/repos/{CONTROL_REPO}/contents/{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "apex-public-runner")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        fail(f"control-plane API request failed with status {exc.code}")
    except Exception as exc:  # noqa: BLE001
        fail(f"control-plane API request failed: {type(exc).__name__}")


def control_token() -> str:
    token = os.environ.get("APEX_CONTROL_TOKEN", "")
    if not token:
        fail("APEX_CONTROL_TOKEN is required")
    return token


def decode_content(record: object, label: str) -> tuple[dict, str]:
    if not isinstance(record, dict) or "content" not in record:
        fail(f"{label} is malformed")
    try:
        data = json.loads(base64.b64decode(record["content"]).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"{label} is invalid: {type(exc).__name__}")
    if not isinstance(data, dict):
        fail(f"{label} must contain a JSON object")
    return data, str(record.get("sha", ""))


def verify_approval(job_id: str, pillar: str, approval_id: str) -> None:
    content, _ = decode_content(api(f"approvals/{approval_id}.json", control_token()), "approval record")
    if content.get("approved") is not True:
        fail("approval is not active")
    if content.get("job_id") != job_id or content.get("pillar") != pillar:
        fail("approval does not match this job")
    print("Private dual-confirmation record verified.")


def validate_plan(plan: object) -> dict:
    if not isinstance(plan, dict):
        fail("plan must be a JSON object")
    job_id = str(plan.get("job_id", ""))
    if not JOB_ID.fullmatch(job_id):
        fail("invalid job_id")
    if plan.get("pillar") not in ALLOWED_TASKS:
        fail("invalid plan pillar")
    if not REPO.fullmatch(str(plan.get("source_repo", ""))):
        fail("invalid plan source repository")
    if not REF.fullmatch(str(plan.get("source_ref", ""))):
        fail("invalid plan source ref")
    missing_provenance = sorted(field for field in PROVENANCE_FIELDS if not str(plan.get(field, "")))
    if missing_provenance:
        fail(f"plan provenance is incomplete: {', '.join(missing_provenance)}")
    return plan


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def assert_new_result(job_id: str) -> None:
    if not JOB_ID.fullmatch(job_id):
        fail("invalid job_id")
    token = control_token()
    if api(f"claims/{job_id}.json", token, allow_not_found=True) is not None:
        fail("an immutable claim already exists for this job_id")
    if api(f"results/{job_id}.json", token, allow_not_found=True) is not None:
        fail("an immutable private result already exists for this job_id")
    print("Replay guard passed: job_id has no prior claim or result.")


def claim_job(plan_path: Path) -> None:
    plan_path = plan_path.resolve()
    if not plan_path.is_file():
        fail("plan file does not exist")
    try:
        plan = validate_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        fail(f"plan file is invalid JSON at line {exc.lineno}")

    job_id = str(plan["job_id"])
    token = control_token()
    if api(f"results/{job_id}.json", token, allow_not_found=True) is not None:
        fail("an immutable private result already exists for this job_id")
    if api(f"claims/{job_id}.json", token, allow_not_found=True) is not None:
        fail("an immutable claim already exists for this job_id")

    claim = {
        "schema_version": "1.0",
        "job_id": job_id,
        "state": "claimed",
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": canonical_sha256(plan),
        "pillar": plan["pillar"],
        "action": plan.get("action", ""),
        "adapter": plan.get("adapter", ""),
        "task": plan.get("task", ""),
        "source_repo": plan["source_repo"],
        "source_ref": plan["source_ref"],
        "provenance": provenance(plan),
    }
    encoded = json.dumps(claim, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    payload = {
        "message": f"runner: claim immutable job {job_id}",
        "content": base64.b64encode(encoded).decode("ascii"),
    }
    api(f"claims/{job_id}.json", token, method="PUT", payload=payload)
    print("Immutable private job claim created.")


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


def provenance(plan: dict) -> dict:
    return {key: plan.get(key, "") for key in sorted(PROVENANCE_FIELDS) if plan.get(key, "")}


def execute(plan: dict, root: Path, result_path: Path) -> int:
    result = {
        "schema_version": "1.1",
        "job_id": plan["job_id"],
        "pillar": plan["pillar"],
        "task": plan["task"],
        "source_repo": plan["source_repo"],
        "source_ref": plan["source_ref"],
        "resolved_source_sha": os.environ.get("APEX_RESOLVED_SOURCE_SHA", ""),
        "provenance": provenance(plan),
        "status": "completed",
    }
    exit_code = 0
    if plan["task"] == "hash-manifest":
        manifest = []
        for path in files(root):
            relative = path.relative_to(root).as_posix()
            manifest.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
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
            proc = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=1800, check=False)
            combined = (proc.stdout + "\n" + proc.stderr)[-100_000:]
            result["command"] = command
            result["exit_code"] = proc.returncode
            result["output_sha256"] = hashlib.sha256(combined.encode()).hexdigest()
            result["output_tail"] = combined[-32_000:]
            if proc.returncode:
                result["status"] = "failed"
                exit_code = proc.returncode
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Job {plan['job_id']} finished with status {result['status']}.")
    return exit_code


def publish(job_id: str, result_path: Path) -> None:
    if not JOB_ID.fullmatch(job_id):
        fail("invalid job_id")
    result_path = result_path.resolve()
    if not result_path.is_file():
        fail("result file does not exist")
    raw = result_path.read_bytes()
    if len(raw) > MAX_RESULT_BYTES:
        fail(f"result exceeds {MAX_RESULT_BYTES} bytes")
    try:
        result = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"result file is invalid JSON: {type(exc).__name__}")
    if not isinstance(result, dict) or result.get("job_id") != job_id:
        fail("result job_id does not match the publish request")
    if "receipt" in result:
        fail("untrusted result already contains a receipt")
    if not isinstance(result.get("status"), str):
        fail("result status is missing")
    result_provenance = result.get("provenance")
    if not isinstance(result_provenance, dict):
        fail("result provenance is missing")
    missing_provenance = sorted(field for field in PROVENANCE_FIELDS if not str(result_provenance.get(field, "")))
    if missing_provenance:
        fail(f"result provenance is incomplete: {', '.join(missing_provenance)}")

    resolved_source_sha = str(result.get("resolved_source_sha", "")).lower()
    stage_outcomes = result.get("stage_outcomes")
    pre_checkout_block = isinstance(stage_outcomes, dict) and stage_outcomes.get("checkout") != "success"
    if pre_checkout_block:
        if resolved_source_sha and not SOURCE_SHA.fullmatch(resolved_source_sha):
            fail("optional resolved source SHA is invalid")
    elif not SOURCE_SHA.fullmatch(resolved_source_sha):
        fail("adapter result is missing a valid resolved source SHA")

    token = control_token()
    result_path_remote = f"results/{job_id}.json"
    if api(result_path_remote, token, allow_not_found=True) is not None:
        fail("immutable result path already exists")

    claim_record = api(f"claims/{job_id}.json", token)
    claim, claim_blob_sha = decode_content(claim_record, "immutable claim")
    if claim.get("job_id") != job_id or claim.get("state") != "claimed":
        fail("immutable claim does not match the result")
    for field in ("pillar", "source_repo", "source_ref", "task"):
        if claim.get(field) != result.get(field):
            fail(f"immutable claim {field} does not match the result")
    for field in ("action", "adapter"):
        if str(claim.get(field, "")) != str(result.get(field, "")):
            fail(f"immutable claim {field} does not match the result")
    claim_provenance = claim.get("provenance")
    if not isinstance(claim_provenance, dict) or claim_provenance != result_provenance:
        fail("immutable claim provenance does not match the result")

    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result["receipt"] = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "claim_path": f"claims/{job_id}.json",
        "claim_blob_sha": claim_blob_sha,
        "plan_sha256": claim.get("plan_sha256", ""),
        "resolved_source_sha": resolved_source_sha,
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "public_runner_sha": os.environ.get("GITHUB_SHA", ""),
        "execution_repo": os.environ.get("GITHUB_REPOSITORY", ""),
    }
    published = json.dumps(result, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    payload = {
        "message": f"runner: record immutable result {job_id}",
        "content": base64.b64encode(published).decode("ascii"),
    }
    api(result_path_remote, token, method="PUT", payload=payload)
    print("Immutable result published to the private control plane.")


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

    replay = sub.add_parser("assert-new-result")
    replay.add_argument("--job-id", required=True)

    claim = sub.add_parser("claim-job")
    claim.add_argument("--plan", default=".apex-plan.json")

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
        emit_outputs(load_plan(args.event, manual))
        return 0
    if args.command == "verify-approval":
        verify_approval(args.job_id, args.pillar, args.approval_id)
        return 0
    if args.command == "assert-new-result":
        assert_new_result(args.job_id)
        return 0
    if args.command == "claim-job":
        claim_job(Path(args.plan))
        return 0
    if args.command == "run":
        return execute(json.loads(Path(args.plan).read_text(encoding="utf-8")), Path(args.workspace), Path(args.result))
    publish(args.job_id, Path(args.result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
