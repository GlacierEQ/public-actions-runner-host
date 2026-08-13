from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import action_face_checkout_workload as checkout
from scripts import keymaster_oidc_token as keymaster
from scripts import revoke_github_installation_token as revoke


def git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return process.stdout.strip()


def test_public_workload_bypass_is_exact_and_read_only(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert keymaster._public_workload_bypass(
        "GlacierEQ/anthropic-agent-coordinator",
        "public-action-workload",
        {"contents": "read"},
    ) is True
    text = output.read_text(encoding="utf-8")
    assert f"token={keymaster.PUBLIC_WORKLOAD_SENTINEL}" in text
    assert "receipt_id=public-workload-no-token" in text

    assert keymaster._public_workload_bypass(
        "GlacierEQ/coreweave-circuit-breaker",
        "public-action-workload",
        {"contents": "read"},
    ) is False
    assert keymaster._public_workload_bypass(
        "GlacierEQ/anthropic-agent-coordinator",
        "public-action-control",
        {"contents": "read"},
    ) is False
    with pytest.raises(keymaster.TokenBrokerError, match="public_workload_permission_exceeds_read"):
        keymaster._public_workload_bypass(
            "GlacierEQ/anthropic-agent-coordinator",
            "public-action-workload",
            {"contents": "write"},
        )


def test_credential_free_checkout_uses_no_auth_header(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-propagate")
    monkeypatch.setenv("APEX_WORKLOAD_TOKEN", keymaster.PUBLIC_WORKLOAD_SENTINEL)
    env = checkout.fetch_environment(None)
    assert "GITHUB_TOKEN" not in env
    assert "APEX_WORKLOAD_TOKEN" not in env
    assert "GIT_CONFIG_COUNT" not in env
    assert not any("AUTHORIZATION" in value.upper() for value in env.values())

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    git(source, "config", "user.name", "APEX test")
    git(source, "config", "user.email", "apex@example.invalid")
    (source / "payload.txt").write_text("public\n", encoding="utf-8")
    git(source, "add", "payload.txt")
    git(source, "commit", "-m", "fixture")
    source_sha = git(source, "rev-parse", "HEAD")

    workspace = tmp_path / "workload"
    resolved = checkout.checkout_repository(
        "GlacierEQ/anthropic-agent-coordinator",
        source_sha,
        workspace,
        None,
        remote_url=source.as_uri(),
    )
    assert resolved == source_sha
    config = (workspace / ".git/config").read_text(encoding="utf-8").lower()
    assert "extraheader" not in config
    assert "authorization" not in config


def test_public_sentinel_revocation_is_a_noop(monkeypatch) -> None:
    class ForbiddenOpener:
        def open(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("public workload sentinel must not call GitHub revocation API")

    monkeypatch.setenv(revoke.TOKEN_ENV, revoke.PUBLIC_WORKLOAD_SENTINEL)
    monkeypatch.setattr(revoke, "_OPENER", ForbiddenOpener())
    assert revoke.main() == 0


def test_public_sentinel_constants_remain_identical() -> None:
    assert checkout.PUBLIC_WORKLOAD_SENTINEL == keymaster.PUBLIC_WORKLOAD_SENTINEL
    assert revoke.PUBLIC_WORKLOAD_SENTINEL == keymaster.PUBLIC_WORKLOAD_SENTINEL
