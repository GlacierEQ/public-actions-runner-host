#!/usr/bin/env python3
"""Validate and normalize a public-runner job for one private GlacierEQ repository."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

SCHEMA = "glaciereq.private-ci-relay.v1"
ALLOWED_FIELDS = {
    "schema",
    "job_id",
    "workload_repo",
    "workload_ref",
    "verify_script",
    "language",
    "python_version",
    "node_version",
    "timeout_minutes",
    "status_context",
}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
REPO_RE = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[A-Za-z0-9][A-Za-z0-9._/-]{0,199})$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}$")
STATUS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
LANGUAGES = {"python", "typescript", "javascript", "generic"}
VERIFY_PREFIXES = ("scripts/ci/", ".github/verification/")


def fail(message: str) -> "NoReturn":
    raise SystemExit(message)


def safe_verify_script(raw: object) -> str:
    value = str(raw or "").strip()
    if not value or value.startswith("/"):
        fail("verify_script must be a repository-relative path")
    path = PurePosixPath(value)
    if ".." in path.parts:
        fail("verify_script cannot traverse outside the repository")
    if not any(value.startswith(prefix) for prefix in VERIFY_PREFIXES):
        fail("verify_script must live under scripts/ci/ or .github/verification/")
    if path.suffix not in {".sh", ".py"}:
        fail("verify_script must be a .sh or .py repository-owned verifier")
    return value


def normalize(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        fail("job payload must be a JSON object")
    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        fail("unknown fields: " + ", ".join(unknown))
    if payload.get("schema") != SCHEMA:
        fail(f"schema must equal {SCHEMA!r}")

    job_id = str(payload.get("job_id") or "").strip()
    if not JOB_ID_RE.fullmatch(job_id):
        fail("job_id must be 8-128 safe characters")

    workload_repo = str(payload.get("workload_repo") or "").strip()
    if not REPO_RE.fullmatch(workload_repo):
        fail("workload_repo must be an exact GlacierEQ repository name")

    workload_ref = str(payload.get("workload_ref") or "").strip()
    if (
        not REF_RE.fullmatch(workload_ref)
        or ".." in workload_ref
        or "//" in workload_ref
        or workload_ref.endswith("/")
    ):
        fail("workload_ref is not a safe Git ref or exact SHA")

    verify_script = safe_verify_script(payload.get("verify_script"))

    language = str(payload.get("language") or "generic").strip().lower()
    if language not in LANGUAGES:
        fail("language must be python, typescript, javascript, or generic")

    python_version = str(payload.get("python_version") or "3.12").strip()
    node_version = str(payload.get("node_version") or "22").strip()
    if not VERSION_RE.fullmatch(python_version):
        fail("python_version is invalid")
    if not VERSION_RE.fullmatch(node_version):
        fail("node_version is invalid")

    try:
        timeout_minutes = int(payload.get("timeout_minutes", 20))
    except (TypeError, ValueError):
        fail("timeout_minutes must be an integer")
    if not 1 <= timeout_minutes <= 45:
        fail("timeout_minutes must be between 1 and 45")

    status_context = str(payload.get("status_context") or "APEX/public-ci-relay").strip()
    if not STATUS_RE.fullmatch(status_context):
        fail("status_context is invalid")

    return {
        "schema": SCHEMA,
        "job_id": job_id,
        "workload_repo": workload_repo,
        "workload_ref": workload_ref,
        "verify_script": verify_script,
        "language": language,
        "python_version": python_version,
        "node_version": node_version,
        "timeout_minutes": timeout_minutes,
        "status_context": status_context,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    try:
        payload = json.loads(args.job.read_text(encoding="utf-8"))
        normalized = normalize(payload)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid job file: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(normalized, sort_keys=True))
    if args.github_output:
        args.github_output.parent.mkdir(parents=True, exist_ok=True)
        with args.github_output.open("a", encoding="utf-8") as handle:
            for key, value in normalized.items():
                handle.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
