from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "github-app" / "bootstrap_apex_github_app.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_apex_github_app", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_manifest_is_owner_only_and_least_privilege() -> None:
    manifest = bootstrap.load_manifest(
        ROOT / "github-app" / "app-manifest.json",
        "http://127.0.0.1:8765/callback",
    )

    assert manifest["name"] == "APEX Runner Bridge"
    assert manifest["public"] is False
    assert manifest["default_events"] == []
    assert manifest["default_permissions"] == {"contents": "write"}
    assert manifest["request_oauth_on_install"] is False
    assert manifest["setup_on_update"] is False
    assert manifest["hook_attributes"] == {"active": False}


def test_registration_state_is_separate_from_manifest_payload() -> None:
    manifest = bootstrap.load_manifest(
        ROOT / "github-app" / "app-manifest.json",
        "http://127.0.0.1:8765/callback",
    )
    page = bootstrap.registration_page(manifest, "state-token").decode("utf-8")

    assert 'name="state" value="state-token"' in page
    assert '"state":"state-token"' not in page
    assert 'action="https://github.com/settings/apps/new"' in page


def test_private_key_is_sent_only_through_stdin(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run_gh(
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del check
        calls.append((args, input_text))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(bootstrap, "run_gh", fake_run_gh)
    private_key = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"
    bootstrap.write_repository_settings("Iv1.client", private_key)

    assert len(calls) == 2
    secret_args, secret_stdin = calls[1]
    assert bootstrap.PRIVATE_KEY_SECRET in secret_args
    assert "--body" not in secret_args
    assert private_key not in secret_args
    assert secret_stdin == private_key


def test_installation_allowlist_is_exact() -> None:
    assert bootstrap.EXPECTED_REPOSITORIES == frozenset(
        {
            "GlacierEQ/mastermind",
            "GlacierEQ/llm-runner-teams",
            "GlacierEQ/monolith",
            "GlacierEQ/MEGA-PDF",
        }
    )
