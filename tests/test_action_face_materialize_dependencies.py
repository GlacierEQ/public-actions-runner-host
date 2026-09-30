from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import action_face_materialize_dependencies as deps
from scripts import action_face_plan


def test_tool_system_action_declares_exact_read_dependency() -> None:
    entries = [
        item
        for item in action_face_plan.catalog_actions()
        if item.get("action") == "code.tool-system.validate"
    ]
    assert len(entries) == 1
    assert entries[0]["read_dependencies"] == [
        {
            "repository": "GlacierEQ/AKOS",
            "ref": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
        }
    ]


def test_plan_carries_catalog_dependencies_but_job_cannot_inject_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "action": "",
                "client_payload": {
                    "job_id": "computer-user-deps-001",
                    "pillar": "C",
                    "action": "code.tool-system.validate",
                    "source_ref": "a" * 40,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    plan = action_face_plan.build_plan(str(event), {})
    assert plan["read_dependencies"] == [
        {
            "repository": "GlacierEQ/AKOS",
            "ref": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
        }
    ]

    injected = {
        "job_id": "computer-user-deps-002",
        "pillar": "C",
        "action": "code.tool-system.validate",
        "source_ref": "a" * 40,
        "read_dependencies": [{"repository": "GlacierEQ/evil", "ref": "main"}],
    }
    with pytest.raises(SystemExit, match="unknown fields"):
        action_face_plan.validate_shape(injected)


def test_materializer_checks_out_each_dependency_then_revokes_its_token(
    tmp_path: Path,
) -> None:
    calls: list[tuple] = []

    def mint(repository: str) -> str:
        calls.append(("mint", repository))
        return f"token-for-{repository}"

    def checkout(repository: str, ref: str, path: Path, token: str) -> str:
        calls.append(("checkout", repository, ref, path.name, token))
        path.mkdir(parents=True)
        (path / "README.md").write_text(repository, encoding="utf-8")
        return {
            "GlacierEQ/AKOS": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
            "GlacierEQ/apex-control-plane": "b" * 40,
        }[repository]

    def revoke(token: str) -> None:
        calls.append(("revoke", token))

    manifest = deps.materialize(
        [
            {
                "repository": "GlacierEQ/AKOS",
                "ref": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
            },
            {
                "repository": "GlacierEQ/apex-control-plane",
                "ref": "b" * 40,
            },
        ],
        root=tmp_path,
        mint=mint,
        checkout=checkout,
        revoke=revoke,
    )

    assert [item["repository"] for item in manifest] == [
        "GlacierEQ/AKOS",
        "GlacierEQ/apex-control-plane",
    ]
    assert (tmp_path / "AKOS" / "README.md").is_file()
    assert (tmp_path / "apex-control-plane" / "README.md").is_file()
    assert calls == [
        ("mint", "GlacierEQ/AKOS"),
        (
            "checkout",
            "GlacierEQ/AKOS",
            "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
            "AKOS",
            "token-for-GlacierEQ/AKOS",
        ),
        ("revoke", "token-for-GlacierEQ/AKOS"),
        ("mint", "GlacierEQ/apex-control-plane"),
        (
            "checkout",
            "GlacierEQ/apex-control-plane",
            "b" * 40,
            "apex-control-plane",
            "token-for-GlacierEQ/apex-control-plane",
        ),
        ("revoke", "token-for-GlacierEQ/apex-control-plane"),
    ]


def test_materializer_revokes_token_even_when_checkout_fails(tmp_path: Path) -> None:
    revoked: list[str] = []

    def checkout(*_args, **_kwargs):
        raise RuntimeError("checkout failed")

    with pytest.raises(RuntimeError, match="checkout failed"):
        deps.materialize(
            [{"repository": "GlacierEQ/AKOS", "ref": "main"}],
            root=tmp_path,
            mint=lambda _repo: "ephemeral",
            checkout=checkout,
            revoke=revoked.append,
        )

    assert revoked == ["ephemeral"]


@pytest.mark.parametrize(
    "value",
    [
        {"repository": "OtherOrg/AKOS", "ref": "main"},
        {"repository": "GlacierEQ/../AKOS", "ref": "main"},
        {"repository": "GlacierEQ/AKOS", "ref": "../main"},
        {"repository": "GlacierEQ/AKOS", "ref": "main;rm -rf /"},
    ],
)
def test_materializer_rejects_untrusted_dependency_specs(value: dict) -> None:
    with pytest.raises(ValueError):
        deps.validate_dependencies([value])


def test_materialized_dependencies_are_not_allowed_to_equal_primary_workload() -> None:
    with pytest.raises(ValueError, match="primary workload"):
        deps.validate_dependencies(
            [{"repository": "GlacierEQ/computer-user", "ref": "main"}],
            source_repo="GlacierEQ/computer-user",
        )
