from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import action_face_materialize_dependencies as deps


def dep(repository: str, sha: str = "a" * 40) -> dict[str, str]:
    return {
        "repository": repository,
        "source_ref": "main",
        "source_sha": sha,
    }


def write_manifest(root: Path, dependencies: list[dict[str, str]]) -> None:
    path = root / "scripts" / "ci" / "private_dependencies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "glaciereq.ci-private-dependencies.v1",
                "dependencies": dependencies,
            }
        ),
        encoding="utf-8",
    )


def test_exact_workload_source_owns_dependency_declaration(tmp_path: Path) -> None:
    expected = [
        dep(
            "GlacierEQ/AKOS",
            "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
        ),
        dep(
            "GlacierEQ/pro-code",
            "7e16c85b91ae7b8e0b8a6ed20d54087caf165af9",
        ),
    ]
    write_manifest(tmp_path, expected)

    assert deps.load_workload_dependencies(
        tmp_path,
        source_repo="GlacierEQ/computer-user",
    ) == expected


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

    def checkout(
        repository: str,
        source_sha: str,
        path: Path,
        token: str | None,
    ) -> str:
        calls.append(("checkout", repository, source_sha, path.name, token))
        path.mkdir(parents=True)
        return source_sha

    def revoke(token: str) -> None:
        calls.append(("revoke", token))

    manifest = deps.materialize(
        [
            dep(
                "GlacierEQ/AKOS",
                "6c91b098292c08c49a88f4e98ab9cb2f850013e1",
            ),
            dep("GlacierEQ/apex-control-plane", "b" * 40),
        ],
        root=tmp_path,
        mint=mint,
        checkout=checkout,
        revoke=revoke,
        is_public=lambda _repo: False,
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
            [dep("GlacierEQ/AKOS")],
            root=tmp_path,
            mint=lambda _repo: "ephemeral",
            checkout=checkout,
            revoke=revoked.append,
            is_public=lambda _repo: False,
        )

    assert revoked == ["ephemeral"]


@pytest.mark.parametrize(
    "value",
    [
        dep("OtherOrg/AKOS"),
        dep("GlacierEQ/../AKOS"),
        {
            "repository": "GlacierEQ/AKOS",
            "source_ref": "../main",
            "source_sha": "a" * 40,
        },
        {
            "repository": "GlacierEQ/AKOS",
            "source_ref": "main",
            "source_sha": "MAIN",
        },
    ],
)
def test_materializer_rejects_untrusted_dependency_specs(value: dict) -> None:
    with pytest.raises(ValueError):
        deps.validate_dependencies([value])


def test_materialized_dependencies_cannot_equal_primary_workload() -> None:
    with pytest.raises(ValueError, match="primary workload"):
        deps.validate_dependencies(
            [dep("GlacierEQ/computer-user")],
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

    def checkout(
        repository: str,
        source_sha: str,
        path: Path,
        token: str | None,
    ) -> str:
        calls.append(("checkout", repository, token))
        path.mkdir(parents=True)
        return source_sha

    staged = deps.materialize(
        [dep("GlacierEQ/pro-code")],
        root=tmp_path,
        is_public=lambda _repo: True,
        mint=lambda _repo: (_ for _ in ()).throw(
            AssertionError("token must not be minted")
        ),
        checkout=checkout,
        revoke=lambda _token: (_ for _ in ()).throw(
            AssertionError("nothing to revoke")
        ),
    )

    assert staged[0]["repository"] == "GlacierEQ/pro-code"
    assert calls == [("checkout", "GlacierEQ/pro-code", None)]


def test_private_dependency_uses_ephemeral_token(tmp_path: Path) -> None:
    calls: list[tuple] = []

    deps.materialize(
        [dep("GlacierEQ/AKOS", "b" * 40)],
        root=tmp_path,
        is_public=lambda _repo: False,
        mint=lambda repo: calls.append(("mint", repo)) or "ephemeral",
        checkout=lambda repo, sha, path, token: calls.append(
            ("checkout", repo, token)
        )
        or sha,
        revoke=lambda token: calls.append(("revoke", token)),
    )

    assert calls == [
        ("mint", "GlacierEQ/AKOS"),
        ("checkout", "GlacierEQ/AKOS", "ephemeral"),
        ("revoke", "ephemeral"),
    ]


def test_private_checkout_uses_git_askpass_without_token_in_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        calls.append((command, env))
        if command[-2:] == ["rev-parse", "HEAD"]:
            return "a" * 40
        if "status" in command:
            return ""
        return ""

    monkeypatch.setattr(deps, "_run", fake_run)
    target = tmp_path / "AKOS"
    deps._checkout_with_token(
        "GlacierEQ/AKOS",
        "a" * 40,
        target,
        "super-secret-token",
    )

    serialized = "\n".join(" ".join(command) for command, _env in calls)
    assert "super-secret-token" not in serialized
    fetch_env = next(
        env
        for command, env in calls
        if command[:3] == ["git", "-C", str(target)] and "fetch" in command
    )
    assert fetch_env is not None
    assert fetch_env["GIT_TERMINAL_PROMPT"] == "0"
    assert fetch_env["GIT_ASKPASS"]
    assert fetch_env["APEX_GIT_USERNAME"] == "x-access-token"
    assert fetch_env["APEX_GIT_PASSWORD"] == "super-secret-token"
