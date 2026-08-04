#!/usr/bin/env python3
"""Verify runner, workload, and result integrity before private publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import apex_pillar_runner as base

SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_STATUS = {"completed", "failed", "blocked", "skipped"}


def fail(message: str) -> None:
    raise SystemExit(f"POSTRUN_GUARD_BLOCK: {message}")


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
        shell=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.devnull,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    if process.returncode != 0:
        fail(f"git {' '.join(args)} failed for {root.name}")
    return process.stdout.strip()


def verify_checkout(root: Path, expected_sha: str, label: str) -> dict[str, str]:
    if not root.is_dir() or root.is_symlink():
        fail(f"{label} checkout directory does not exist or is unsafe")
    head = git(root, "rev-parse", "HEAD").lower()
    if head != expected_sha:
        fail(f"{label} HEAD does not match the expected commit")
    dirty = git(root, "status", "--porcelain=v1", "--untracked-files=no")
    if dirty:
        fail(f"{label} tracked files changed during workload execution")
    diff = git(root, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
    return {
        "head": head,
        "tracked_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner-root", required=True)
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--workload-root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--resolved-source-sha", default="")
    args = parser.parse_args()

    workflow_sha = os.environ.get("GITHUB_SHA", "").lower()
    if not SHA.fullmatch(workflow_sha):
        fail("GITHUB_SHA is not a full commit SHA")

    runner_root = Path(args.runner_root).resolve()
    control_root = Path(args.control_root).resolve()
    workload_root = Path(args.workload_root).resolve()
    verify_checkout(runner_root, workflow_sha, "primary action-face")
    verify_checkout(control_root, workflow_sha, "fresh post-run control")

    plan_path = Path(args.plan)
    result_path = Path(args.result)
    expected_plan = runner_root / ".apex-plan.json"
    expected_result_dir = runner_root / ".apex-results"
    if plan_path.is_symlink() or plan_path.resolve() != expected_plan:
        fail("plan path is not the canonical action-face plan")
    if result_path.is_symlink() or result_path.resolve().parent != expected_result_dir:
        fail("result path is outside the canonical result directory")
    if not plan_path.resolve().is_file() or not result_path.resolve().is_file():
        fail("plan or result file does not exist")

    plan = base.validate_plan(json.loads(plan_path.read_text(encoding="utf-8")))
    if plan.get("job_id") != args.job_id:
        fail("plan job ID does not match the workflow request")

    claim, _ = base.decode_content(
        base.api(f"claims/{args.job_id}.json", base.control_token()),
        "immutable claim",
    )
    if claim.get("plan_sha256") != base.canonical_sha256(plan):
        fail("current plan does not match the immutable claim hash")
    for field in (
        "job_id",
        "pillar",
        "action",
        "adapter",
        "task",
        "source_repo",
        "source_ref",
    ):
        if str(claim.get(field, "")) != str(plan.get(field, "")):
            fail(f"claim and plan disagree on {field}")
    if claim.get("provenance") != base.provenance(plan):
        fail("claim and plan provenance disagree")

    raw = result_path.read_bytes()
    if len(raw) > base.MAX_RESULT_BYTES:
        fail("result exceeds the maximum private receipt size")
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        fail("result must be a JSON object")
    if result.get("job_id") != args.job_id:
        fail("result job ID does not match")
    if result.get("status") not in ALLOWED_STATUS:
        fail("result status is not governed")
    if "receipt" in result:
        fail("untrusted result contains a receipt")
    for field in (
        "pillar",
        "action",
        "adapter",
        "task",
        "source_repo",
        "source_ref",
    ):
        if str(result.get(field, "")) != str(plan.get(field, "")):
            fail(f"result and plan disagree on {field}")
    if result.get("provenance") != base.provenance(plan):
        fail("result and plan provenance disagree")

    resolved = str(result.get("resolved_source_sha", "")).lower()
    expected_resolved = str(args.resolved_source_sha or "").lower()
    stage_outcomes = result.get("stage_outcomes")
    pre_adapter_block = isinstance(stage_outcomes, dict) and (
        stage_outcomes.get("checkout") != "success"
        or stage_outcomes.get("checkout_binding") != "success"
    )
    if pre_adapter_block:
        if resolved and not SHA.fullmatch(resolved):
            fail("optional resolved source SHA is invalid")
    else:
        if not SHA.fullmatch(expected_resolved):
            fail("workflow did not provide a valid resolved source SHA")
        if resolved != expected_resolved:
            fail("result resolved source SHA does not match checkout binding")
        workload_attestation = verify_checkout(
            workload_root,
            expected_resolved,
            "private workload",
        )
        output("workload_verified", "true")
        output(
            "workload_tracked_diff_sha256",
            workload_attestation["tracked_diff_sha256"],
        )

    digest = hashlib.sha256(raw).hexdigest()
    output("result_file_sha256", digest)
    output("verified", "true")
    print(f"POSTRUN_GUARD_OK: {args.job_id} result locked as {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
