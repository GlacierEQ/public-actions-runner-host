from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import workload_isolation

EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


def git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(root.parent / "git-home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()


def committed_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "workload"
    repository.mkdir()
    (tmp_path / "git-home").mkdir(exist_ok=True)
    git(repository, "init")
    git(repository, "config", "user.name", "Runner Test")
    git(repository, "config", "user.email", "runner@example.invalid")
    (repository / "tracked.txt").write_text("canonical\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", "canonical source")
    return repository, git(repository, "rev-parse", "HEAD").lower()


def test_attestation_accepts_exact_clean_checkout(tmp_path: Path) -> None:
    repository, sha = committed_repository(tmp_path)

    attestation = workload_isolation.attest_workspace(repository, sha)

    assert attestation["resolved_source_sha"] == sha
    assert attestation["tracked_clean"] is True
    assert attestation["tracked_diff_sha256"] == EMPTY_SHA256
    assert isinstance(attestation["checkout_device"], int)
    assert isinstance(attestation["checkout_inode"], int)


def test_attestation_rejects_tracked_source_mutation(tmp_path: Path) -> None:
    repository, sha = committed_repository(tmp_path)
    (repository / "tracked.txt").write_text("mutated\n", encoding="utf-8")

    with pytest.raises(
        workload_isolation.WorkloadIsolationError,
        match="tracked workload files changed",
    ):
        workload_isolation.attest_workspace(repository, sha)


def test_attestation_rejects_commit_drift(tmp_path: Path) -> None:
    repository, sha = committed_repository(tmp_path)
    (repository / "second.txt").write_text("second\n", encoding="utf-8")
    git(repository, "add", "second.txt")
    git(repository, "commit", "-m", "second commit")

    with pytest.raises(
        workload_isolation.WorkloadIsolationError,
        match="workload HEAD changed",
    ):
        workload_isolation.attest_workspace(repository, sha)


def test_attestation_rejects_symlinked_checkout(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    repository, sha = committed_repository(real_parent)
    linked_checkout = tmp_path / "linked-workload"
    linked_checkout.symlink_to(repository, target_is_directory=True)

    with pytest.raises(
        workload_isolation.WorkloadIsolationError,
        match="open failed without following symlinks",
    ):
        workload_isolation.attest_workspace(linked_checkout, sha)


def test_attestation_rejects_symlinked_parent_component(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    _, sha = committed_repository(real_parent)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(
        workload_isolation.WorkloadIsolationError,
        match="directory traversal failed without following symlinks",
    ):
        workload_isolation.attest_workspace(linked_parent / "workload", sha)


def test_checkout_must_remain_under_declared_parent(tmp_path: Path) -> None:
    allowed_parent = tmp_path / "allowed"
    allowed_parent.mkdir()
    other_parent = tmp_path / "other"
    other_parent.mkdir()
    repository, _ = committed_repository(other_parent)

    with pytest.raises(
        workload_isolation.WorkloadIsolationError,
        match="not a direct child",
    ):
        workload_isolation.open_checkout(
            repository,
            allowed_parent=allowed_parent,
            label="workload",
        )


def test_held_checkout_detects_visible_path_replacement(tmp_path: Path) -> None:
    repository, sha = committed_repository(tmp_path)
    checkout = workload_isolation.open_checkout(repository, label="workload")
    with checkout:
        original_inode = checkout.inode
        moved = tmp_path / "original-workload"
        repository.rename(moved)
        repository.mkdir()
        assert checkout.inode == original_inode
        assert (checkout.proc_path / "tracked.txt").read_text(encoding="utf-8") == (
            "canonical\n"
        )
        with pytest.raises(
            workload_isolation.WorkloadIsolationError,
            match="visible checkout path was replaced",
        ):
            checkout.assert_path_identity()
        with pytest.raises(
            workload_isolation.WorkloadIsolationError,
            match="visible checkout path was replaced",
        ):
            workload_isolation.attest_checkout(checkout, sha)


def test_relative_read_rejects_file_symlink(tmp_path: Path) -> None:
    repository, _ = committed_repository(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do-not-read\n", encoding="utf-8")
    (repository / "leak.txt").symlink_to(outside)

    with workload_isolation.open_checkout(repository, label="workload") as checkout:
        with pytest.raises(
            workload_isolation.WorkloadIsolationError,
            match="open/read failed without following symlinks",
        ):
            workload_isolation.read_relative_regular_file(checkout, "leak.txt")


def test_untracked_runtime_artifacts_do_not_change_source_attestation(
    tmp_path: Path,
) -> None:
    repository, sha = committed_repository(tmp_path)
    (repository / "runtime-output.tmp").write_text("ephemeral\n", encoding="utf-8")

    attestation = workload_isolation.attest_workspace(repository, sha)

    assert attestation["tracked_clean"] is True
