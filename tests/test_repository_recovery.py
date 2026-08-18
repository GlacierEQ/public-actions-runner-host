from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recover_deleted_glaciereq_repos.py"
spec = importlib.util.spec_from_file_location("repo_recovery", MODULE_PATH)
assert spec and spec.loader
recovery = importlib.util.module_from_spec(spec)
# dataclasses resolves forward-annotation metadata through sys.modules while the module
# executes. Register the module exactly as a normal import would before exec_module().
sys.modules[spec.name] = recovery
spec.loader.exec_module(recovery)


def test_same_repository_id_redirect_is_not_collision(monkeypatch):
    calls = []

    def fake_repo(name):
        if name == "Z-BACKUP-Digital-Forensics-Report":
            return 200, {"id": 1014553287, "name": "Z-BACKUP-Digital-Forensics-Report", "archived": True}
        if name == "Digital-Forensics-Report":
            # GitHub old-name redirect: same numeric repository object.
            return 200, {"id": 1014553287, "name": "Z-BACKUP-Digital-Forensics-Report", "archived": True}
        raise AssertionError(name)

    def fake_patch(repo_name, **changes):
        calls.append((repo_name, changes))
        return 200, {"id": 1014553287, "name": "Digital-Forensics-Report", "archived": False}

    monkeypatch.setattr(recovery, "repo", fake_repo)
    monkeypatch.setattr(recovery, "patch_repo", fake_patch)

    result = recovery.activate("Z-BACKUP-Digital-Forensics-Report")

    assert result.state == "RENAMED_REDIRECT_ACTIVE"
    assert result.repository_id == 1014553287
    assert result.target_collision is False
    assert result.final_name == "Digital-Forensics-Report"
    assert calls == [
        (
            "Z-BACKUP-Digital-Forensics-Report",
            {"name": "Digital-Forensics-Report", "archived": False},
        )
    ]


def test_true_different_id_collision_preserves_both_histories(monkeypatch):
    calls = []

    def fake_repo(name):
        if name == "Z-BACKUP-example":
            return 200, {"id": 111, "name": "Z-BACKUP-example", "archived": True}
        if name == "example":
            return 200, {"id": 222, "name": "example", "archived": False}
        if name == "example-recovered-full":
            return 404, {"message": "Not Found"}
        raise AssertionError(name)

    def fake_patch(repo_name, **changes):
        calls.append((repo_name, changes))
        return 200, {"id": 111, "name": "example-recovered-full", "archived": False}

    monkeypatch.setattr(recovery, "repo", fake_repo)
    monkeypatch.setattr(recovery, "patch_repo", fake_patch)

    result = recovery.activate("Z-BACKUP-example")

    assert result.state == "RENAMED_COLLISION_PRESERVED"
    assert result.repository_id == 111
    assert result.target_collision is True
    assert result.final_name == "example-recovered-full"
    assert calls == [
        (
            "Z-BACKUP-example",
            {"name": "example-recovered-full", "archived": False},
        )
    ]


def test_deleted_original_is_never_recreated(monkeypatch):
    monkeypatch.setattr(recovery, "repo", lambda name: (404, {"message": "Not Found"}))

    def forbidden_patch(*args, **kwargs):
        raise AssertionError("missing original must not be replaced or mutated")

    monkeypatch.setattr(recovery, "patch_repo", forbidden_patch)
    result = recovery.activate("Z-BACKUP-Federal-Forensic-Framework")
    assert result.state == "RESTORE_REQUIRED"
    assert result.final_name is None


def test_forensic_lane_contains_reconstructed_deleted_and_surviving_frontier():
    selected = recovery.selected_targets(SimpleNamespace(forensics=True, full=False))
    required = {
        "Z-BACKUP-FEDERAL-FORENSIC-REPAIR-OMNIBUS",
        "Z-BACKUP-Digital-Forensics-Report",
        "Z-BACKUP-digital-forensics-labs",
        "Z-BACKUP-ios-forensics-mcp",
        "Z-BACKUP-DesktopCommanderMCP",
        "Z-BACKUP-Elcomsoft-Phone-Breaker-Mobile-Forensic-Analysis",
        "Z-BACKUP-Federal-Forensic-Framework",
        "Z-BACKUP-Federal-Forensic-MCP-Master",
        "Z-BACKUP-android-forensics",
        "Z-BACKUP-forensic_transcriber",
        "Z-BACKUP-mba-desktop-forensics",
        "Z-BACKUP-sharepoint-forensic-mastermind",
        "Z-BACKUP-desktop-commander-mcp",
        "Z-BACKUP-apex-mcp-server",
        "Z-BACKUP-ULTIMATE-REPAIR-APEX",
    }
    assert required.issubset(set(selected))
    assert len(selected) == len(set(selected))


def test_forensic_lane_does_not_expand_into_unrelated_backup_families():
    selected = recovery.selected_targets(SimpleNamespace(forensics=True, full=False))
    assert "Z-BACKUP-apex-vault" not in selected
    assert "Z-BACKUP-mastermind-colossus" not in selected
