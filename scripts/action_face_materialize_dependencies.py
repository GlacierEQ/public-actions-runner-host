#!/usr/bin/env python3
"""Materialize exact read-only private dependencies for one trusted workload.

The exact workload source owns .apex/read-dependencies.json. The public runner
satisfies that declaration generically with short-lived one-repository tokens,
then exposes only local paths through APEX_DEPENDENCY_MANIFEST.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

SOURCE_MANIFEST = ".apex/read-dependencies.json"
SOURCE_SCHEMA = "glaciereq.workload-read-dependencies.v1"
STAGED_SCHEMA = "glaciereq.ci-staged-dependencies.v1"
REPO_RE = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_DEPENDENCIES = 12


def _fail(message: str) -> None:
    raise ValueError(message)


def validate_dependencies(
    raw: object,
    *,
    source_repo: str | None = None,
) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        _fail("dependencies must be a list")
    if len(raw) > MAX_DEPENDENCIES:
        _fail(f"dependencies exceed maximum of {MAX_DEPENDENCIES}")

    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"repository", "source_sha"}:
            _fail("each dependency must contain only repository and source_sha")
        repository = str(item.get("repository") or "").strip()
        source_sha = str(item.get("source_sha") or "").strip().lower()
        if not REPO_RE.fullmatch(repository):
            _fail("dependency repository must be an exact GlacierEQ repository")
        if source_repo and repository == source_repo:
            _fail("dependency cannot equal the primary workload")
        if repository in seen:
            _fail("dependency repositories must be unique")
        if not SHA_RE.fullmatch(source_sha):
            _fail("dependency source_sha must be an exact lowercase commit SHA")
        seen.add(repository)
        output.append({"repository": repository, "source_sha": source_sha})
    return output


def load_workload_dependencies(
    workload_dir: Path,
    *,
    source_repo: str | None = None,
) -> list[dict[str, str]]:
    path = workload_dir / SOURCE_MANIFEST
    if not path.is_file():
        return []
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SOURCE_SCHEMA:
        _fail("workload dependency manifest schema is invalid")
    if set(payload) != {"schema", "dependencies"}:
        _fail("workload dependency manifest contains unknown fields")
    return validate_dependencies(payload.get("dependencies"), source_repo=source_repo)


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
        shell=False,
    )
    output = result.stdout or ""
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command[:3])}\n"
            f"{output[-6000:]}"
        )
    return output.strip()


def _checkout_with_token(
    repository: str,
    source_sha: str,
    path: Path,
    token: str,
) -> str:
    if path.exists():
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    remote = f"https://github.com/{repository}.git"
    _run(["git", "init", "-q", str(path)], env=env)
    _run(["git", "-C", str(path), "remote", "add", "origin", remote], env=env)
    _run(
        ["git", "-C", str(path), "fetch", "--depth=1", "origin", source_sha],
        env=env,
    )
    _run(
        ["git", "-C", str(path), "checkout", "-q", "--detach", "FETCH_HEAD"],
        env=env,
    )
    observed = _run(["git", "-C", str(path), "rev-parse", "HEAD"]).lower()
    if observed != source_sha:
        raise RuntimeError(
            f"dependency SHA mismatch for {repository}: {observed} != {source_sha}"
        )
    status = _run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=no"]
    )
    if status:
        raise RuntimeError(f"dependency checkout is not clean: {repository}")
    return observed


def _mint_via_keymaster(repository: str) -> str:
    root = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile(prefix="apex-dep-token-", delete=False) as handle:
        output_path = Path(handle.name)
    try:
        env = os.environ.copy()
        env["GITHUB_OUTPUT"] = str(output_path)
        _run(
            [
                sys.executable,
                str(root / "scripts" / "keymaster_oidc_token.py"),
                "--repository",
                repository,
                "--permission",
                "contents=read",
                "--operation",
                "public-action-workload",
            ],
            env=env,
        )
        values: dict[str, str] = {}
        for line in output_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        token = values.get("token", "")
        if not token:
            raise RuntimeError(f"Keymaster returned no token for {repository}")
        print(f"::add-mask::{token}")
        return token
    finally:
        output_path.unlink(missing_ok=True)


def _revoke_token(token: str) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["GITHUB_INSTALLATION_TOKEN"] = token
    _run(
        [sys.executable, str(root / "scripts" / "revoke_github_installation_token.py")],
        env=env,
    )


def materialize(
    dependencies: list[dict[str, str]],
    *,
    root: Path,
    mint: Callable[[str], str] = _mint_via_keymaster,
    checkout: Callable[[str, str, Path, str], str] = _checkout_with_token,
    revoke: Callable[[str], None] = _revoke_token,
) -> list[dict[str, str]]:
    validated = validate_dependencies(dependencies)
    root.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, str]] = []

    for item in validated:
        repository = item["repository"]
        source_sha = item["source_sha"]
        token = mint(repository)
        try:
            path = root / repository.split("/", 1)[1]
            observed = checkout(repository, source_sha, path, token)
            staged.append(
                {
                    "repository": repository,
                    "source_sha": observed,
                    "path": str(path.resolve()),
                }
            )
        finally:
            revoke(token)
            token = ""
    return staged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload-dir", type=Path, default=Path("workload"))
    parser.add_argument("--source-repo", required=True)
    parser.add_argument(
        "--staging-root",
        type=Path,
        default=Path(".apex-dependencies"),
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=Path(".apex-dependencies/manifest.json"),
    )
    args = parser.parse_args()

    dependencies = load_workload_dependencies(
        args.workload_dir,
        source_repo=args.source_repo,
    )
    staged = materialize(dependencies, root=args.staging_root) if dependencies else []
    payload = {
        "schema": STAGED_SCHEMA,
        "dependencies": staged,
    }
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = str(args.manifest_out.resolve())
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    if github_env:
        with Path(github_env).open("a", encoding="utf-8") as handle:
            handle.write(f"APEX_DEPENDENCY_MANIFEST={manifest_path}\n")
    print(
        json.dumps(
            {
                "schema": "glaciereq.action-face.dependency-materialization.v1",
                "dependency_count": len(staged),
                "manifest": manifest_path,
                "repositories": [item["repository"] for item in staged],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
