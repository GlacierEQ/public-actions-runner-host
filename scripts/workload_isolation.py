"""Build capability-minimized environments and hold immutable checkout identities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_EXTRA_ENV = {"APEX_RESOLVED_SOURCE_SHA"}
FORBIDDEN_AMBIENT_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
    "AKOS_POLICY_SHA256",
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "APEX_RUNNER_APP_PRIVATE_KEY",
    "GITHUB_ENV",
    "GITHUB_OUTPUT",
    "GITHUB_TOKEN",
    "GH_PAT",
}
FORBIDDEN_AMBIENT_PREFIXES = ("ACTIONS_", "GITHUB_")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


class WorkloadIsolationError(RuntimeError):
    """Raised when a workload boundary cannot be established or re-attested."""


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _open_directory_chain(path: Path, label: str) -> int:
    """Open an absolute directory component-by-component without following links."""
    absolute = _absolute_without_resolving(path)
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component,
                DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except OSError as error:
        os.close(descriptor)
        raise WorkloadIsolationError(
            f"{label} directory traversal failed without following symlinks: "
            f"{type(error).__name__}"
        ) from error
    return descriptor


@dataclass
class CheckoutHandle:
    """An opened checkout whose inode remains bound for the full operation."""

    raw_path: Path
    parent_path: Path
    parent_fd: int
    fd: int
    label: str
    device: int
    inode: int
    _closed: bool = False

    @property
    def proc_path(self) -> Path:
        return Path(f"/proc/self/fd/{self.fd}")

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return (self.fd, self.parent_fd)

    def assert_path_identity(self) -> None:
        if self._closed:
            raise WorkloadIsolationError(f"{self.label} checkout handle is closed")
        try:
            held = os.fstat(self.fd)
            visible = os.stat(
                self.raw_path.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise WorkloadIsolationError(
                f"{self.label} checkout path identity is unavailable: "
                f"{type(error).__name__}"
            ) from error
        if not stat.S_ISDIR(held.st_mode) or not stat.S_ISDIR(visible.st_mode):
            raise WorkloadIsolationError(
                f"{self.label} checkout is no longer a regular directory"
            )
        expected = (self.device, self.inode)
        if (held.st_dev, held.st_ino) != expected:
            raise WorkloadIsolationError(
                f"{self.label} held checkout identity changed unexpectedly"
            )
        if (visible.st_dev, visible.st_ino) != expected:
            raise WorkloadIsolationError(
                f"{self.label} visible checkout path was replaced during execution"
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.fd)
        os.close(self.parent_fd)

    def __enter__(self) -> CheckoutHandle:
        self.assert_path_identity()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_checkout(
    path: Path,
    *,
    allowed_parent: Path | CheckoutHandle | None = None,
    label: str = "workload",
) -> CheckoutHandle:
    """Open one direct-child checkout through held no-follow descriptors."""
    raw = _absolute_without_resolving(path)
    if isinstance(allowed_parent, CheckoutHandle):
        allowed_parent.assert_path_identity()
        parent_path = allowed_parent.raw_path
        parent_fd = os.dup(allowed_parent.fd)
    else:
        parent_path = _absolute_without_resolving(allowed_parent or raw.parent)
        parent_fd = _open_directory_chain(parent_path, f"{label} parent")

    if raw.parent != parent_path:
        os.close(parent_fd)
        raise WorkloadIsolationError(
            f"{label} checkout is not a direct child of its allowed parent"
        )

    try:
        descriptor = os.open(raw.name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
    except OSError as error:
        os.close(parent_fd)
        raise WorkloadIsolationError(
            f"{label} checkout open failed without following symlinks: "
            f"{type(error).__name__}"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        os.close(parent_fd)
        raise WorkloadIsolationError(f"{label} checkout is not a regular directory")

    handle = CheckoutHandle(
        raw_path=raw,
        parent_path=parent_path,
        parent_fd=parent_fd,
        fd=descriptor,
        label=label,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )
    try:
        handle.assert_path_identity()
    except WorkloadIsolationError:
        handle.close()
        raise
    return handle


def read_regular_file(
    parent: CheckoutHandle,
    name: str,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Read one direct regular file through its held parent directory."""
    if not name or name in {".", ".."} or Path(name).name != name:
        raise WorkloadIsolationError("regular file name is invalid")
    parent.assert_path_identity()
    try:
        descriptor = os.open(name, FILE_FLAGS, dir_fd=parent.fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkloadIsolationError("requested file is not regular")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise WorkloadIsolationError("regular file exceeds its size ceiling")
            chunks.append(chunk)
    except OSError as error:
        raise WorkloadIsolationError(
            f"regular file open failed without following symlinks: "
            f"{type(error).__name__}"
        ) from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    parent.assert_path_identity()
    return b"".join(chunks)


def _secure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise WorkloadIsolationError(
            f"sandbox path is not a regular directory: {path.name}"
        )
    path.chmod(0o700)
    return path


def _assert_environment_is_capability_minimized(environment: Mapping[str, str]) -> None:
    forbidden = sorted(
        key
        for key in environment
        if key in FORBIDDEN_AMBIENT_ENV
        or (key not in SAFE_EXTRA_ENV and key.startswith(FORBIDDEN_AMBIENT_PREFIXES))
    )
    if forbidden:
        raise WorkloadIsolationError(
            "forbidden ambient authority entered workload environment: "
            + ", ".join(forbidden)
        )


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

    _assert_environment_is_capability_minimized(environment)
    return environment


def _git(checkout: CheckoutHandle, *args: str) -> str:
    git_home = _secure_directory(checkout.parent_path / ".apex-git-home")
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
        raise WorkloadIsolationError(
            f"workload git {' '.join(args)} failed with exit {process.returncode}"
        )
    return process.stdout.strip()


def attest_checkout(
    checkout: CheckoutHandle,
    expected_sha: str,
) -> dict[str, object]:
    """Require the exact bound commit and a clean tracked private source tree."""
    checkout.assert_path_identity()
    expected_sha = expected_sha.lower()
    if not SHA.fullmatch(expected_sha):
        raise WorkloadIsolationError("expected workload SHA is invalid")

    head = _git(checkout, "rev-parse", "HEAD").lower()
    if head != expected_sha:
        raise WorkloadIsolationError("workload HEAD changed after checkout binding")

    tracked_status = _git(
        checkout,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )
    if tracked_status:
        raise WorkloadIsolationError("tracked workload files changed during execution")

    diff = _git(checkout, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
    checkout.assert_path_identity()
    return {
        "resolved_source_sha": head,
        "tracked_clean": True,
        "tracked_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "checkout_device": checkout.device,
        "checkout_inode": checkout.inode,
    }


def attest_workspace(
    workspace: Path,
    expected_sha: str,
    *,
    allowed_parent: Path | CheckoutHandle | None = None,
) -> dict[str, object]:
    """Convenience attestation for callers that do not execute after validation."""
    with open_checkout(
        workspace,
        allowed_parent=allowed_parent,
        label="workload",
    ) as checkout:
        return attest_checkout(checkout, expected_sha)


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
