from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import workload_isolation


def git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    (tmp_path / "git-home").mkdir()
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

    assert attestation == {
        "resolved_source_sha": sha,
        "tracked_clean": True,
        "tracked_diff_sha256": (
            "e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855"
        ),
    }


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


def test_untracked_runtime_artifacts_do_not_change_source_attestation(
    tmp_path: Path,
) -> None:
    repository, sha = committed_repository(tmp_path)
    (repository / "runtime-output.tmp").write_text("ephemeral\n", encoding="utf-8")

    attestation = workload_isolation.attest_workspace(repository, sha)

    assert attestation["tracked_clean"] is True
