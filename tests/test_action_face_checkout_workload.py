from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts import action_face_checkout_workload as checkout


def git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return process.stdout.strip()


def test_credential_environment_is_process_only(monkeypatch) -> None:
    monkeypatch.setenv("APEX_WORKLOAD_TOKEN", "must-not-propagate")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-propagate-either")
    env = checkout.credential_environment("unit-token")
    assert "APEX_WORKLOAD_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
    assert "unit-token" not in env["GIT_CONFIG_VALUE_0"]


def test_checkout_ignores_malformed_gitmodules_without_persisting_token(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    git(source, "config", "user.name", "APEX test")
    git(source, "config", "user.email", "apex@example.invalid")
    (source / "payload.txt").write_text("verified\n", encoding="utf-8")
    (source / ".gitmodules").write_text(
        '[submodule "MCP-SuperAssistant"]\n\tpath = MCP-SuperAssistant\n',
        encoding="utf-8",
    )
    git(source, "add", "payload.txt", ".gitmodules")
    git(source, "commit", "-m", "fixture")
    source_sha = git(source, "rev-parse", "HEAD")

    workspace = tmp_path / "workload"
    token = "private-unit-token"
    resolved = checkout.checkout_repository(
        "GlacierEQ/MEGA-PDF",
        source_sha,
        workspace,
        token,
        remote_url=source.as_uri(),
    )

    assert resolved == source_sha
    assert (workspace / "payload.txt").read_text(encoding="utf-8") == "verified\n"
    assert git(workspace, "status", "--porcelain", "--untracked-files=no") == ""
    config = (workspace / ".git/config").read_text(encoding="utf-8")
    assert token not in config
    assert "extraheader" not in config.lower()
    assert os.path.isfile(workspace / ".gitmodules")
