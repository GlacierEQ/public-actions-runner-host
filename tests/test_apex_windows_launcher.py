import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_launcher_is_one_click_and_never_prompts_for_a_key() -> None:
    cmd = (ROOT / "START_APEX_RUNNER_BRIDGE.cmd").read_text(encoding="utf-8")
    ps1 = (ROOT / "github-app" / "start_apex_runner_bridge.ps1").read_text(
        encoding="utf-8"
    )

    assert "start_apex_runner_bridge.ps1" in cmd
    assert "bootstrap_apex_github_app.py" in ps1
    for token in ("auth", "login", "--web"):
        assert token in ps1
    assert "Read-Host" not in ps1
    assert "set /p" not in cmd.lower()
    assert "APEX_RUNNER_APP_PRIVATE_KEY" not in cmd
    assert "APEX_RUNNER_APP_PRIVATE_KEY" not in ps1
    assert "PRIVATE KEY-----" not in cmd
    assert "PRIVATE KEY-----" not in ps1


def test_windows_launcher_keeps_only_browser_consent_as_human_boundary() -> None:
    ps1 = (ROOT / "github-app" / "start_apex_runner_bridge.ps1").read_text(
        encoding="utf-8"
    )

    required = [
        "auth', 'login', '--web'",
        "--manifest",
        "Invoke-Checked -Executable $python",
        "No private key is displayed, copied, pasted, written to disk",
    ]
    for fragment in required:
        assert fragment in ps1


def test_windows_launcher_uses_pinned_activation_target() -> None:
    ps1 = (ROOT / "github-app" / "start_apex_runner_bridge.ps1").read_text(
        encoding="utf-8"
    )
    target = json.loads(
        (ROOT / "github-app" / "activation-target.json").read_text(encoding="utf-8")
    )

    assert "activation-target.json" in ps1
    assert "ConvertFrom-Json" in ps1
    assert "--run-id" in ps1
    assert "workflow_run_id" in ps1
    assert "source_ref must be a full lowercase commit SHA" in ps1
    assert target["workflow"] == "APEX Public Action Face"
    assert target["workflow_run_id"] == 31170532956
    assert target["issue_number"] == 95
    assert target["action"] == "code.monolith.validate-legal-live-reconciliation"
    assert target["source_repo"] == "GlacierEQ/monolith"
    assert target["source_ref"] == "f686505aa521faf5f9511e423124832ad08aae3a"
