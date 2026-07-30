#!/usr/bin/env python3
"""Normalize one owner-created same-repository PR job envelope.

The pull request must target main and change exactly one regular file named
jobs/<job_id>.json. Control-plane code, workflows, and unrelated paths cannot be
changed in an execution PR.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import action_face_plan as planner
import apex_pillar_runner as base

JOB_PATH = re.compile(r"^jobs/([A-Za-z0-9][A-Za-z0-9._-]{7,63})\.json$")
MAX_JOB_BYTES = 4096


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pull_request = event.get("pull_request") or {}
    base_record = pull_request.get("base") or {}
    head_record = pull_request.get("head") or {}
    repository = os.environ.get("GITHUB_REPOSITORY", "")

    if (base_record.get("repo") or {}).get("full_name") != repository:
        base.fail("pull-request base repository does not match the public action face")
    if (head_record.get("repo") or {}).get("full_name") != repository:
        base.fail("pull-request head must be a same-repository branch")
    if str(base_record.get("ref") or "") != "main":
        base.fail("pull-request job ingress must target main")

    base_sha = str(base_record.get("sha") or "")
    head_sha = str(head_record.get("sha") or "")
    if not base.SOURCE_SHA.fullmatch(base_sha) or not base.SOURCE_SHA.fullmatch(head_sha):
        base.fail("pull-request base/head SHA is missing or invalid")

    changed_output = git("diff", "--name-only", f"{base_sha}...{head_sha}")
    changed = [line.strip() for line in changed_output.splitlines() if line.strip()]
    if len(changed) != 1:
        base.fail("an execution pull request must change exactly one file and no unrelated paths")

    relative = changed[0]
    match = JOB_PATH.fullmatch(relative)
    if not match:
        base.fail("the only changed path must be jobs/<job_id>.json")
    job_id_from_path = match.group(1)

    path = Path(relative)
    jobs_root = Path("jobs").resolve()
    resolved = path.resolve()
    if path.is_symlink() or resolved.parent != jobs_root:
        base.fail("pull-request job path is not a regular file directly under jobs/")
    if not resolved.is_file():
        base.fail("pull-request job file does not exist")
    if resolved.stat().st_size > MAX_JOB_BYTES:
        base.fail(f"pull-request job envelope exceeds {MAX_JOB_BYTES} bytes")

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        base.fail(f"pull-request job file is not valid JSON: line {exc.lineno} column {exc.colno}")
    if not isinstance(payload, dict):
        base.fail("pull-request job envelope must be a JSON object")
    if payload.get("job_id") != job_id_from_path:
        base.fail("pull-request job_id must match the jobs/<job_id>.json filename")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        empty_event = handle.name
    try:
        plan = planner.build_plan(empty_event, payload)
    finally:
        Path(empty_event).unlink(missing_ok=True)

    plan["pull_request_number"] = str(pull_request.get("number") or event.get("number") or "")
    base.emit_outputs(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
