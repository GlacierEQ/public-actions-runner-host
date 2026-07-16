#!/usr/bin/env python3
"""Verify the checked-out workload and emit its exact immutable commit SHA."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]+$")


def fail(message: str) -> None:
    raise SystemExit(f"CHECKOUT_BIND_BLOCK: {message}")


def git(workspace: Path, *args: str) -> str:
    try:
        process = subprocess.run(
            ["git", "-C", str(workspace), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
            env={key: value for key, value in os.environ.items() if key not in {"APEX_CONTROL_TOKEN", "APEX_PRIVATE_READ_TOKEN", "GH_PAT", "GITHUB_TOKEN"}},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"git command failed to start: {type(exc).__name__}")
    if process.returncode != 0:
        fail(f"git {' '.join(args)} failed")
    return process.stdout.strip()


def normalized_remote(url: str) -> str:
    value = url.strip()
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.endswith(".git"):
        value = value[:-4]
    return value.strip("/")


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default=".apex-plan.json")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    workspace = Path(args.workspace).resolve()
    if not plan_path.is_file():
        fail("plan file does not exist")
    if not workspace.is_dir():
        fail("workload directory does not exist")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    source_repo = str(plan.get("source_repo", ""))
    if not REPO.fullmatch(source_repo):
        fail("plan source repository is invalid")

    inside = git(workspace, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        fail("workload is not a Git work tree")

    resolved_sha = git(workspace, "rev-parse", "HEAD").lower()
    if not SHA.fullmatch(resolved_sha):
        fail("resolved workload commit is not a full SHA-1")

    remote = git(workspace, "config", "--get", "remote.origin.url")
    if normalized_remote(remote) != source_repo:
        fail("checkout origin does not match the catalog-approved source repository")

    dirty = git(workspace, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        fail("checked-out tracked files are not clean")

    output("resolved_source_sha", resolved_sha)
    output("source_repo", source_repo)
    print(f"CHECKOUT_BIND_OK: {source_repo}@{resolved_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
