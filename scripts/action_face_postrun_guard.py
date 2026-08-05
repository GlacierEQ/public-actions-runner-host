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
from workload_isolation import (
    CheckoutHandle,
    WorkloadIsolationError,
    open_checkout,
    read_regular_file,
)

SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_STATUS = {"completed", "failed", "blocked", "skipped"}


def fail(message: str) -> None:
    raise SystemExit(f"POSTRUN_GUARD_BLOCK: {message}")


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def open_bound_checkout(
    path: Path,
    label: str,
    *,
    allowed_parent: Path | CheckoutHandle | None = None,
) -> CheckoutHandle:
    try:
        return open_checkout(path, allowed_parent=allowed_parent, label=label)
    except WorkloadIsolationError as error:
        fail(str(error))


def git(checkout: CheckoutHandle, *args: str) -> str:
    git_home = checkout.parent_path / ".apex-postrun-git-home"
    git_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if git_home.is_symlink() or not git_home.is_dir():
        fail("post-run Git HOME is unsafe")
    git_home.chmod(0o700)

    checkout.assert_path_identity()
    process = subprocess.run(
        ["git", "-C", str(checkout.proc_path), *args],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        shell=False,
        pass_fds=checkout.pass_fds,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(git_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    checkout.assert_path_identity()
    if process.returncode != 0:
        fail(f"git {' '.join(args)} failed for {checkout.label}")
    return process.stdout.strip()


def verify_checkout(
    checkout: CheckoutHandle,
    expected_sha: str,
    label: str,
) -> dict[str, str | int]:
    head = git(checkout, "rev-parse", "HEAD").lower()
    if head != expected_sha:
        fail(f"{label} HEAD does not match the expected commit")
    dirty = git(checkout, "status", "--porcelain=v1", "--untracked-files=no")
    if dirty:
        fail(f"{label} tracked files changed during workload execution")
    diff = git(checkout, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
    checkout.assert_path_identity()
    return {
        "head": head,
        "tracked_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "checkout_device": checkout.device,
        "checkout_inode": checkout.inode,
    }


def direct_file_name(path: Path, parent: CheckoutHandle, label: str) -> str:
    raw = Path(os.path.abspath(os.fspath(path)))
    if raw.parent != parent.raw_path or raw.name in {"", ".", ".."}:
        fail(f"{label} is outside its canonical directory")
    return raw.name


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

    runner_argument = Path(args.runner_root)
    runner_parent = Path(os.path.abspath(os.fspath(runner_argument))).parent
    runner_handle = open_bound_checkout(
        runner_argument,
        "primary action-face",
        allowed_parent=runner_parent,
    )
    with runner_handle:
        control_handle = open_bound_checkout(
            Path(args.control_root),
            "fresh post-run control",
            allowed_parent=runner_handle,
        )
        workload_handle = open_bound_checkout(
            Path(args.workload_root),
            "private workload",
            allowed_parent=runner_handle,
        )
        result_directory_handle = open_bound_checkout(
            Path(args.result).parent,
            "result directory",
            allowed_parent=runner_handle,
        )
        with control_handle, workload_handle, result_directory_handle:
            verify_checkout(runner_handle, workflow_sha, "primary action-face")
            verify_checkout(
                control_handle,
                workflow_sha,
                "fresh post-run control",
            )

            plan_name = direct_file_name(Path(args.plan), runner_handle, "plan path")
            result_name = direct_file_name(
                Path(args.result),
                result_directory_handle,
                "result path",
            )
            try:
                plan_raw = read_regular_file(
                    runner_handle,
                    plan_name,
                    max_bytes=base.MAX_RESULT_BYTES,
                )
                raw = read_regular_file(
                    result_directory_handle,
                    result_name,
                    max_bytes=base.MAX_RESULT_BYTES,
                )
            except WorkloadIsolationError as error:
                fail(str(error))

            plan = base.validate_plan(json.loads(plan_raw.decode("utf-8")))
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
                    workload_handle,
                    expected_resolved,
                    "private workload",
                )
                output("workload_verified", "true")
                output(
                    "workload_tracked_diff_sha256",
                    str(workload_attestation["tracked_diff_sha256"]),
                )
                output(
                    "workload_checkout_device",
                    str(workload_attestation["checkout_device"]),
                )
                output(
                    "workload_checkout_inode",
                    str(workload_attestation["checkout_inode"]),
                )

            runner_handle.assert_path_identity()
            control_handle.assert_path_identity()
            workload_handle.assert_path_identity()
            result_directory_handle.assert_path_identity()

    digest = hashlib.sha256(raw).hexdigest()
    output("result_file_sha256", digest)
    output("verified", "true")
    print(f"POSTRUN_GUARD_OK: {args.job_id} result locked as {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
