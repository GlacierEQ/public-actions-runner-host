from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import action_face_materialize_dependencies as deps


def write_manifest(root: Path, dependencies: list[dict[str, str]]) -> None:
    path = root / ".apex" / "read-dependencies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "glaciereq.workload-read-dependencies.v1",
                "dependencies": dependencies,
            }
        ),
        encoding="utf-8",
    )


def test_exact_workload_source_owns_dependency_declaration(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        [
            {
                "repository": "GlacierEQ/AKOS",
                "source_sha": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
            },
            {
                "repository": "GlacierEQ/pro-code",
                "source_sha": "7e16c85b91ae7b8e0b8a6ed20d54087caf165af9",
            },
        ],
    )

    assert deps.load_workload_dependencies(
        tmp_path,
        source_repo="GlacierEQ/computer-user",
    ) == [
        {
            "repository": "GlacierEQ/AKOS",
            "source_sha": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
        },
        {
            "repository": "GlacierEQ/pro-code",
            "source_sha": "7e16c85b91ae7b8e0b8a6ed20d54087caf165af9",
        },
    ]


def test_missing_manifest_means_no_cross_repo_dependencies(tmp_path: Path) -> None:
    assert deps.load_workload_dependencies(
        tmp_path,
        source_repo="GlacierEQ/computer-user",
    ) == []


def test_materializer_checks_out_each_dependency_then_revokes_its_token(
    tmp_path: Path,
) -> None:
    calls: list[tuple] = []

    def mint(repository: str) -> str:
        calls.append(("mint", repository))
        return f"token-for-{repository}"

    def checkout(repository: str, source_sha: str, path: Path, token: str) -> str:
        calls.append(("checkout", repository, source_sha, path.name, token))
        path.mkdir(parents=True)
        (path / "README.md").write_text(repository, encoding="utf-8")
        return source_sha

    def revoke(token: str) -> None:
        calls.append(("revoke", token))

    manifest = deps.materialize(
        [
            {
                "repository": "GlacierEQ/AKOS",
                "source_sha": "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
            },
            {
                "repository": "GlacierEQ/apex-control-plane",
                "source_sha": "b" * 40,
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
    assert calls[0] == ("mint", "GlacierEQ/AKOS")
    assert calls[2] == ("revoke", "token-for-GlacierEQ/AKOS")
    assert calls[3] == ("mint", "GlacierEQ/apex-control-plane")
    assert calls[5] == ("revoke", "token-for-GlacierEQ/apex-control-plane")


def test_materializer_revokes_token_even_when_checkout_fails(tmp_path: Path) -> None:
    revoked: list[str] = []

    def checkout(*_args, **_kwargs):
        raise RuntimeError("checkout failed")

    with pytest.raises(RuntimeError, match="checkout failed"):
        deps.materialize(
            [{"repository": "GlacierEQ/AKOS", "source_sha": "a" * 40}],
            root=tmp_path,
            mint=lambda _repo: "ephemeral",
            checkout=checkout,
            revoke=revoked.append,
        )

    assert revoked == ["ephemeral"]


@pytest.mark.parametrize(
    "value",
    [
        {"repository": "OtherOrg/AKOS", "source_sha": "a" * 40},
        {"repository": "GlacierEQ/../AKOS", "source_sha": "a" * 40},
        {"repository": "GlacierEQ/AKOS", "source_sha": "../main"},
        {"repository": "GlacierEQ/AKOS", "source_sha": "MAIN"},
    ],
)
def test_materializer_rejects_untrusted_dependency_specs(value: dict) -> None:
    with pytest.raises(ValueError):
        deps.validate_dependencies([value])


def test_materialized_dependencies_cannot_equal_primary_workload() -> None:
    with pytest.raises(ValueError, match="primary workload"):
        deps.validate_dependencies(
            [{"repository": "GlacierEQ/computer-user", "source_sha": "a" * 40}],
            source_repo="GlacierEQ/computer-user",
        )


def test_workflow_materializes_dependencies_before_adapter_execution() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "apex-pillar-runner.yml"
    ).read_text(encoding="utf-8")

    materialize = workflow.index("Materialize workload-declared read dependencies")
    execute = workflow.index("Execute isolated public action adapter")
    assert materialize < execute
    assert "APEX_DEPENDENCY_MANIFEST" in (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "action_face_materialize_dependencies.py"
    ).read_text(encoding="utf-8")



def test_public_dependency_uses_no_token(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def checkout(repository: str, source_sha: str, path: Path, token: str | None) -> str:
        calls.append(("checkout", repository, token))
        path.mkdir(parents=True)
        return source_sha

    staged = deps.materialize(
        [{"repository": "GlacierEQ/pro-code", "source_sha": "a" * 40}],
        root=tmp_path,
        is_public=lambda _repo: True,
        mint=lambda _repo: (_ for _ in ()).throw(AssertionError("token must not be minted")),
        checkout=checkout,
        revoke=lambda _token: (_ for _ in ()).throw(AssertionError("nothing to revoke")),
    )

    assert staged[0]["repository"] == "GlacierEQ/pro-code"
    assert calls == [("checkout", "GlacierEQ/pro-code", None)]


def test_private_dependency_uses_ephemeral_token(tmp_path: Path) -> None:
    calls: list[tuple] = []

    deps.materialize(
        [{"repository": "GlacierEQ/AKOS", "source_sha": "b" * 40}],
        root=tmp_path,
        is_public=lambda _repo: False,
        mint=lambda repo: calls.append(("mint", repo)) or "ephemeral",
        checkout=lambda repo, sha, path, token: calls.append(("checkout", repo, token)) or sha,
        revoke=lambda token: calls.append(("revoke", token)),
    )

    assert calls == [
        ("mint", "GlacierEQ/AKOS"),
        ("checkout", "GlacierEQ/AKOS", "ephemeral"),
        ("revoke", "ephemeral"),
    ]
