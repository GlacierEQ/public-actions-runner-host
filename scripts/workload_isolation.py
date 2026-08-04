"""Build capability-minimized workload environments and attest private checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_EXTRA_ENV = {"APEX_RESOLVED_SOURCE_SHA"}


class WorkloadIsolationError(RuntimeError):
    """Raised when a workload boundary cannot be established or re-attested."""


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    """Reject any symlink in an existing path chain before dereferencing it."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise WorkloadIsolationError(
                f"{label} path contains a symlink component: {candidate.name or candidate}"
            )
        if candidate == candidate.parent:
            break


def secure_checkout_path(
    path: Path,
    *,
    allowed_parent: Path | None = None,
    label: str = "workload",
) -> Path:
    """Resolve one direct-child checkout only after proving its path is non-symlinked."""
    raw = _absolute_without_resolving(path)
    parent = _absolute_without_resolving(allowed_parent or raw.parent)
    _reject_symlink_components(parent, f"{label} parent")
    _reject_symlink_components(raw, label)

    if raw.parent != parent:
        raise WorkloadIsolationError(
            f"{label} checkout is not a direct child of its allowed parent"
        )
    if not raw.exists() or not raw.is_dir():
        raise WorkloadIsolationError(f"{label} checkout is not a regular directory")

    resolved_parent = parent.resolve(strict=True)
    resolved = raw.resolve(strict=True)
    if resolved.parent != resolved_parent:
        raise WorkloadIsolationError(f"{label} checkout escapes its allowed parent")
    return resolved


def _secure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise WorkloadIsolationError(
            f"sandbox path is not a regular directory: {path.name}"
        )
    path.chmod(0o700)
    return path


def build_environment(
    result_path: Path,
    job_id: str,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an explicit workload environment with no ambient Actions authority."""
    if not JOB_ID.fullmatch(job_id):
        raise WorkloadIsolationError("job_id is invalid for workload isolation")

    result_path = result_path.resolve()
    sandbox = _secure_directory(result_path.parent / f"sandbox-{job_id}")
    home = _secure_directory(sandbox / "home")
    temporary = _secure_directory(sandbox / "tmp")
    cache = _secure_directory(sandbox / "cache")
    config = _secure_directory(sandbox / "config")

    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(config),
        "CI": "true",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_CACHE": str(cache / "npm"),
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_USERCONFIG": os.devnull,
        "PIP_CACHE_DIR": str(cache / "pip"),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }

    for key, value in (extra or {}).items():
        if key not in SAFE_EXTRA_ENV:
            raise WorkloadIsolationError(
                f"extra workload environment key is forbidden: {key}"
            )
        if not isinstance(value, str):
            raise WorkloadIsolationError(
                f"extra workload environment value is invalid: {key}"
            )
        environment[key] = value

    return environment


def _git(workspace: Path, *args: str) -> str:
    git_home = _secure_directory(workspace.parent / ".apex-git-home")
    process = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        shell=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(git_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    if process.returncode != 0:
        raise WorkloadIsolationError(
            f"workload git {' '.join(args)} failed with exit {process.returncode}"
        )
    return process.stdout.strip()


def attest_workspace(
    workspace: Path,
    expected_sha: str,
    *,
    allowed_parent: Path | None = None,
) -> dict[str, object]:
    """Require the exact bound commit and a clean tracked private source tree."""
    workspace = secure_checkout_path(
        workspace,
        allowed_parent=allowed_parent,
        label="workload",
    )
    expected_sha = expected_sha.lower()
    if not SHA.fullmatch(expected_sha):
        raise WorkloadIsolationError("expected workload SHA is invalid")

    head = _git(workspace, "rev-parse", "HEAD").lower()
    if head != expected_sha:
        raise WorkloadIsolationError("workload HEAD changed after checkout binding")

    tracked_status = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )
    if tracked_status:
        raise WorkloadIsolationError("tracked workload files changed during execution")

    diff = _git(workspace, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
    return {
        "resolved_source_sha": head,
        "tracked_clean": True,
        "tracked_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }


def command_contract_sha256(
    commands: Sequence[Sequence[str]],
    *,
    volatile_roots: Sequence[Path] = (),
) -> str:
    """Hash a stable command contract without embedding runner-specific paths."""
    normalized: list[list[str]] = []
    replacements = [str(path.resolve()) for path in volatile_roots]
    for command in commands:
        normalized_command: list[str] = []
        for argument in command:
            value = str(argument)
            for index, root in enumerate(replacements):
                value = value.replace(root, f"<VOLATILE_ROOT_{index}>")
            normalized_command.append(value)
        normalized.append(normalized_command)
    encoded = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
