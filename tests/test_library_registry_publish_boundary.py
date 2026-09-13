from __future__ import annotations

import json
import sys
from pathlib import Path

import library_registry_publish as publisher
import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "apex-pillar-runner.yml"
CATALOG = ROOT / "config" / "action-face-actions.json"


def _generated_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workload"
    registry = workspace / "registry"
    registry.mkdir(parents=True)
    (registry / "index.json").write_text('{"count": 60}\n', encoding="utf-8")
    (registry / "top_shelf.json").write_text('[{"id": "s-tier"}]\n', encoding="utf-8")
    return workspace


def test_catalog_binds_publish_action_to_exact_library_repo() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    matches = [
        action
        for action in data["actions"]
        if action.get("action") == "library-links-public-publish"
    ]
    assert matches == [
        {
            "pillar": "C",
            "action": "library-links-public-publish",
            "source_event": "coding-deploy",
            "target_repo": "GlacierEQ/library-of-links",
            "legacy_event": "library-links-public-publish",
            "adapter": "constellation-memory-verify",
            "gate": "standard",
            "approval_required": False,
        }
    ]


def test_workflow_grants_write_only_to_cataloged_library_publish_action() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'elif [ "$APEX_ACTION" = "library-links-public-publish" ]; then' in workflow
    assert 'permission="contents=write"' in workflow
    assert 'operation="library-registry-rebuild"' in workflow
    assert "steps.plan.outputs.action == 'library-links-public-publish'" in workflow
    assert "steps.runner.outcome == 'success'" in workflow
    assert "steps.bind.outcome == 'success'" in workflow
    assert "GITHUB_INSTALLATION_TOKEN: ${{ steps.workload_token.outputs.token }}" in workflow
    assert "git -C workload restore --worktree -- registry/index.json registry/top_shelf.json" in workflow
    assert "steps.library_publish.outcome != 'success'" in workflow


def test_publisher_refuses_when_provider_head_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _generated_workspace(tmp_path)
    expected = "a" * 40
    observed: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
        observed.append((method, path, payload))
        assert token == "scoped-token"
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "b" * 40}}
        raise AssertionError(f"unexpected request after moved-head check: {method} {path}")

    monkeypatch.setenv("GITHUB_INSTALLATION_TOKEN", "scoped-token")
    monkeypatch.setattr(publisher, "request", fake_request)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "library_registry_publish.py",
            "--workspace",
            str(workspace),
            "--source-ref",
            expected,
        ],
    )

    with pytest.raises(SystemExit, match="HEAD_MOVED"):
        publisher.main()

    assert observed == [("GET", "/git/ref/heads/main", None)]


def test_publisher_updates_only_generated_projections_and_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _generated_workspace(tmp_path)
    source_ref = "a" * 40
    new_commit = "c" * 40
    calls: list[tuple[str, str, dict | None]] = []
    ref_reads = 0

    def fake_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
        nonlocal ref_reads
        calls.append((method, path, payload))
        assert token == "scoped-token"
        if method == "GET" and path == "/git/ref/heads/main":
            ref_reads += 1
            return {"object": {"sha": source_ref if ref_reads == 1 else new_commit}}
        if method == "GET" and path == f"/git/commits/{source_ref}":
            return {"tree": {"sha": "tree-parent"}}
        if method == "POST" and path == "/git/trees":
            assert payload is not None
            assert payload["base_tree"] == "tree-parent"
            assert [entry["path"] for entry in payload["tree"]] == [
                "registry/index.json",
                "registry/top_shelf.json",
            ]
            assert all(entry["mode"] == "100644" for entry in payload["tree"])
            assert all(entry["type"] == "blob" for entry in payload["tree"])
            return {"sha": "tree-new"}
        if method == "POST" and path == "/git/commits":
            assert payload == {
                "message": "Rebuild knowledge registry",
                "tree": "tree-new",
                "parents": [source_ref],
            }
            return {"sha": new_commit}
        if method == "PATCH" and path == "/git/refs/heads/main":
            assert payload == {"sha": new_commit, "force": False}
            return {"object": {"sha": new_commit}}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setenv("GITHUB_INSTALLATION_TOKEN", "scoped-token")
    monkeypatch.setattr(publisher, "request", fake_request)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "library_registry_publish.py",
            "--workspace",
            str(workspace),
            "--source-ref",
            source_ref,
        ],
    )

    assert publisher.main() == 0
    assert [path for method, path, _ in calls if method == "PATCH"] == [
        "/git/refs/heads/main"
    ]
    assert ref_reads == 2
