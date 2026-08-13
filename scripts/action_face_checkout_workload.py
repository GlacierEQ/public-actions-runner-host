#!/usr/bin/env python3
"""Checkout one approved workload without persisting credentials."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

REPO = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]+$")
SECRET_ENV = {
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "APEX_WORKLOAD_TOKEN",
    "GH_PAT",
    "GH_TOKEN",
    "GITHUB_TOKEN",
}


class CheckoutError(RuntimeError):
    """Raised when an isolated workload checkout cannot be completed safely."""


def sanitized_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in SECRET_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def credential_environment(token: str) -> dict[str, str]:
    if not token or any(character.isspace() for character in token):
        raise CheckoutError("workload token is unavailable or malformed")
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = sanitized_environment()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credential}",
        }
    )
    return env


def fetch_environment(token: str | None) -> dict[str, str]:
    """Use process-only auth when supplied, otherwise a credential-free environment."""
    return credential_environment(token) if token else sanitized_environment()


def run_git(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> str:
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
            env=env or sanitized_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckoutError(
            f"git command failed to start: {type(error).__name__}"
        ) from error
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "git command failed").strip()
        raise CheckoutError(detail[-2000:])
    return process.stdout.strip()


def checkout_repository(
    source_repo: str,
    source_ref: str,
    workspace: Path,
    token: str | None = None,
    *,
    remote_url: str | None = None,
) -> str:
    if not REPO.fullmatch(source_repo):
        raise CheckoutError("plan source repository is invalid")
    if not source_ref or any(character.isspace() for character in source_ref):
        raise CheckoutError("plan source ref is invalid")

    workspace = workspace.resolve()
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir():
            raise CheckoutError("workload path exists but is not a regular directory")
        if any(workspace.iterdir()):
            raise CheckoutError("workload directory must be empty before checkout")
    else:
        workspace.mkdir(parents=True, mode=0o700)

    source_url = remote_url or f"https://github.com/{source_repo}.git"
    run_git(["git", "init", str(workspace)])
    run_git(["git", "-C", str(workspace), "config", "gc.auto", "0"])
    run_git(["git", "-C", str(workspace), "remote", "add", "origin", source_url])
    run_git(
        [
            "git",
            "-C",
            str(workspace),
            "fetch",
            "--no-tags",
            "--prune",
            "--no-recurse-submodules",
            "--depth=1",
            "origin",
            source_ref,
        ],
        env=fetch_environment(token),
        timeout=300,
    )
    run_git(
        ["git", "-C", str(workspace), "checkout", "--detach", "--force", "FETCH_HEAD"]
    )

    resolved_sha = run_git(["git", "-C", str(workspace), "rev-parse", "HEAD"]).lower()
    tracked_dirty = run_git(
        ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=no"]
    )
    if tracked_dirty:
        raise CheckoutError("checked-out tracked files are not clean")

    config_path = workspace / ".git/config"
    config = config_path.read_text(encoding="utf-8")
    if token and token in config:
        raise CheckoutError("workload credential persisted in Git configuration")
    if "extraheader" in config.lower():
        raise CheckoutError("workload credential header persisted in Git configuration")
    return resolved_sha


def runner_workspace_path(value: str, runner_root: Path | None = None) -> Path:
    root = (
        runner_root or Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd()))
    ).resolve()
    requested = Path(value)
    workspace = (requested if requested.is_absolute() else root / requested).resolve()
    expected = (root / "workload").resolve()
    if workspace != expected:
        raise CheckoutError("workload path must be the fixed runner workload directory")
    return workspace


def prepare_runner_workspace(workspace: Path) -> None:
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir():
            raise CheckoutError("runner workload path is not a regular directory")
        shutil.rmtree(workspace)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default=".apex-plan.json")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    if not plan_path.is_file():
        raise SystemExit("WORKLOAD_CHECKOUT_BLOCK: plan file does not exist")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("WORKLOAD_CHECKOUT_BLOCK: plan file is invalid") from error

    token = os.environ.get("APEX_WORKLOAD_TOKEN") or None
    try:
        workspace = runner_workspace_path(args.workspace)
        prepare_runner_workspace(workspace)
        resolved_sha = checkout_repository(
            str(plan.get("source_repo", "")),
            str(plan.get("source_ref", "")),
            workspace,
            token,
        )
    except CheckoutError as error:
        raise SystemExit(f"WORKLOAD_CHECKOUT_BLOCK: {error}") from error

    auth_mode = "credentialed" if token else "credential-free"
    print(
        "WORKLOAD_CHECKOUT_OK: "
        f"{plan.get('source_repo')}@{resolved_sha} {auth_mode} without persisted credentials"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
