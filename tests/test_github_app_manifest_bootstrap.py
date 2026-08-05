from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

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


def test_manifest_errors_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(bootstrap.BootstrapError, match="Manifest file not found"):
        bootstrap.load_manifest(tmp_path / "missing.json", "http://127.0.0.1/callback")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    with pytest.raises(bootstrap.BootstrapError, match="contains invalid JSON"):
        bootstrap.load_manifest(invalid, "http://127.0.0.1/callback")


def test_registration_state_is_separate_from_manifest_payload() -> None:
    manifest = bootstrap.load_manifest(
        ROOT / "github-app" / "app-manifest.json",
        "http://127.0.0.1:8765/callback",
    )
    page = bootstrap.registration_page(manifest, "state-token").decode("utf-8")

    assert 'name="state" value="state-token"' in page
    assert '"state":"state-token"' not in page
    assert 'action="https://github.com/settings/apps/new"' in page


def test_callback_host_is_loopback_only() -> None:
    bootstrap._validate_loopback_host("127.0.0.1")
    with pytest.raises(bootstrap.BootstrapError, match="loopback-only"):
        bootstrap._validate_loopback_host("0.0.0.0")


def test_run_gh_times_out(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(bootstrap.BootstrapError, match="timed out"):
        bootstrap.run_gh(["auth", "status"], timeout_seconds=1)


def test_private_key_is_sent_only_through_stdin(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None, bool]] = []

    def fake_run_gh(
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int = bootstrap.DEFAULT_GH_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        calls.append((args, input_text, check))
        if args[:2] == [
            "api",
            f"/repos/{bootstrap.TARGET_REPO}/actions/variables/{bootstrap.CLIENT_ID_VARIABLE}",
        ]:
            return subprocess.CompletedProcess(args, 1, "", "not found")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(bootstrap, "run_gh", fake_run_gh)
    private_key = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"
    bootstrap.write_repository_settings("Iv1.client", private_key)

    secret_calls = [call for call in calls if call[0][:2] == ["secret", "set"]]
    assert len(secret_calls) == 1
    secret_args, secret_stdin, _ = secret_calls[0]
    assert bootstrap.PRIVATE_KEY_SECRET in secret_args
    assert "--body" not in secret_args
    assert private_key not in secret_args
    assert secret_stdin == private_key


def test_partial_credential_write_rolls_back_variable(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None, bool]] = []

    def fake_run_gh(
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int = bootstrap.DEFAULT_GH_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        calls.append((args, input_text, check))
        if args[:2] == [
            "api",
            f"/repos/{bootstrap.TARGET_REPO}/actions/variables/{bootstrap.CLIENT_ID_VARIABLE}",
        ]:
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps({"name": bootstrap.CLIENT_ID_VARIABLE, "value": "old-client"}),
                "",
            )
        if args[:2] == ["secret", "set"]:
            raise bootstrap.BootstrapError("secret write failed")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(bootstrap, "run_gh", fake_run_gh)

    with pytest.raises(bootstrap.BootstrapError, match="rolled back"):
        bootstrap.write_repository_settings(
            "new-client",
            "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
        )

    variable_sets = [call for call in calls if call[0][:2] == ["variable", "set"]]
    assert variable_sets[0][0][-1] == "new-client"
    assert variable_sets[-1][0][-1] == "old-client"


def test_installation_allowlist_is_exact() -> None:
    assert bootstrap.EXPECTED_REPOSITORIES == frozenset(
        {
            "GlacierEQ/mastermind",
            "GlacierEQ/llm-runner-teams",
            "GlacierEQ/monolith",
            "GlacierEQ/MEGA-PDF",
        }
    )


def successful_run_payload() -> dict[str, object]:
    steps = [
        {"name": name, "conclusion": "success", "status": "completed"}
        for name in bootstrap.REQUIRED_COMPLETION_RECORDS
    ]
    return {
        "attempt": 2,
        "status": "completed",
        "conclusion": "success",
        "url": "https://example.invalid/run",
        "jobs": [
            {
                "name": "APEX bridge",
                "conclusion": "success",
                "status": "completed",
                "steps": steps,
            }
        ],
    }


def test_completion_contract_accepts_only_all_success() -> None:
    bootstrap.validate_completion_records(successful_run_payload())


@pytest.mark.parametrize("state", ["missing", "skipped", "failed"])
def test_completion_contract_rejects_missing_skipped_or_failed(state: str) -> None:
    payload = successful_run_payload()
    jobs = payload["jobs"]
    assert isinstance(jobs, list)
    steps = jobs[0]["steps"]
    assert isinstance(steps, list)

    if state == "missing":
        steps.pop()
        expected = "missing required completion records"
    else:
        steps[-1]["conclusion"] = state
        expected = "did not succeed"

    with pytest.raises(bootstrap.BootstrapError, match=expected):
        bootstrap.validate_completion_records(payload)


def test_rerun_waits_for_new_attempt(monkeypatch) -> None:
    views = iter(
        [
            {
                "attempt": 1,
                "status": "completed",
                "conclusion": "failure",
                "jobs": [],
                "url": "old",
            },
            {
                "attempt": 1,
                "status": "completed",
                "conclusion": "failure",
                "jobs": [],
                "url": "old",
            },
            successful_run_payload(),
        ]
    )
    rerun_calls: list[list[str]] = []

    monkeypatch.setattr(bootstrap, "_view_run", lambda _run_id: next(views))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)

    def fake_run_gh(
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int = bootstrap.DEFAULT_GH_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[str]:
        del input_text, check, timeout_seconds
        rerun_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(bootstrap, "run_gh", fake_run_gh)

    assert bootstrap.rerun_and_report(123, timeout_seconds=10) == 0
    assert rerun_calls == [
        ["run", "rerun", "123", "--failed", "--repo", bootstrap.TARGET_REPO]
    ]
