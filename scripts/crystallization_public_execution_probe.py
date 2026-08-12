#!/usr/bin/env python3
"""Secretless execution evidence for active public original repositories.

This runner follows repository-native manifests rather than inventing a universal
build contract. Missing reproducibility metadata is INCOMPLETE evidence. A command
that the repository actually declares and that fails is BROKEN evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ENV_ALLOW = {
    "PATH", "LANG", "LC_ALL", "TZ", "TERM", "RUNNER_OS", "RUNNER_ARCH",
    "ImageOS", "ImageVersion", "HOME", "CARGO_HOME", "RUSTUP_HOME",
    "RUSTUP_TOOLCHAIN", "GOROOT", "GOPATH", "JAVA_HOME",
    "JAVA_HOME_17_X64", "JAVA_HOME_21_X64",
}
TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", re.I)
PYTHON_REQUIREMENT_FILES = (
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "dev-requirements.txt", "test-requirements.txt",
)
TEST_EXTRA_NAMES = ("test", "tests", "testing", "dev", "development")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def clean_env(home: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW}
    env.update({
        "HOME": home,
        "CI": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_AUDIT": "false",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def run_step(name: str, command: list[str], cwd: Path, env: dict[str, str], timeout_s: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout_s)
        status = "PASS" if proc.returncode == 0 else "FAIL"
        code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        status, code = "TIMEOUT", None
        stdout, stderr = exc.stdout or "", exc.stderr or ""
    except FileNotFoundError as exc:
        status, code, stdout, stderr = "TOOL_MISSING", None, "", str(exc)
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    combined = (stdout + "\n" + stderr).strip()
    return {
        "name": name,
        "command": command,
        "status": status,
        "exit_code": code,
        "elapsed_s": round(time.monotonic() - started, 3),
        "output_sha256": hashlib.sha256(combined.encode("utf-8", errors="replace")).hexdigest(),
        "output_tail": combined[-4000:],
    }


def synthetic_step(name: str, status: str, message: str) -> dict[str, Any]:
    return {
        "name": name,
        "command": [f"__{status}__"],
        "status": status,
        "exit_code": None,
        "elapsed_s": 0,
        "output_sha256": hashlib.sha256(message.encode()).hexdigest(),
        "output_tail": message,
    }


def package_json(repo: Path) -> dict[str, Any]:
    try:
        value = json.loads((repo / "package.json").read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def pyproject(repo: Path) -> dict[str, Any]:
    path = repo / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        value = tomllib.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def python_test_files(repo: Path) -> list[Path]:
    out: list[Path] = []
    for path in repo.rglob("*.py"):
        try:
            rel = path.relative_to(repo).as_posix()
        except ValueError:
            continue
        if TEST_FILE_RE.search(rel):
            out.append(path)
    return sorted(out)


def text_contains(paths: list[Path], needles: tuple[str, ...]) -> bool:
    for path in paths:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in needles):
            return True
    return False


def manifest_text(repo: Path) -> str:
    paths = [repo / name for name in PYTHON_REQUIREMENT_FILES]
    paths += [repo / "pyproject.toml", repo / "setup.cfg", repo / "setup.py", repo / "tox.ini", repo / "pytest.ini"]
    paths += list((repo / ".github" / "workflows").glob("*.yml"))
    paths += list((repo / ".github" / "workflows").glob("*.yaml"))
    chunks: list[str] = []
    for path in paths:
        if path.is_file():
            try:
                chunks.append(path.read_text(errors="replace").lower())
            except OSError:
                pass
    return "\n".join(chunks)


def dependency_declared(repo: Path, package: str) -> bool:
    text = manifest_text(repo)
    aliases = {package.lower(), package.lower().replace("-", "_")}
    return any(alias in text for alias in aliases)


def preferred_test_extra(repo: Path) -> str | None:
    project = pyproject(repo)
    optional = project.get("project", {}).get("optional-dependencies", {})
    if isinstance(optional, dict):
        for name in TEST_EXTRA_NAMES:
            value = optional.get(name)
            if isinstance(value, list) and value:
                return name
    return None


def python_steps(repo: Path) -> list[tuple[str, list[str], int]]:
    if not list(repo.rglob("*.py")):
        return []
    steps: list[tuple[str, list[str], int]] = [
        ("python_compile", ["python", "-m", "compileall", "-q", "."], 180),
    ]
    tests = python_test_files(repo)
    if not tests:
        return steps

    requirement_files = [name for name in PYTHON_REQUIREMENT_FILES if (repo / name).is_file()]
    for name in requirement_files:
        steps.append((f"python_dependencies:{name}", ["python", "-m", "pip", "install", "-r", name], 300))

    if (repo / "pyproject.toml").is_file():
        extra = preferred_test_extra(repo)
        target = f".[{extra}]" if extra else "."
        steps.append(("python_package_install", ["python", "-m", "pip", "install", "-e", target], 300))

    pytest_contract = (
        text_contains(tests, ("import pytest", "from pytest", "pytest."))
        or (repo / "pytest.ini").is_file()
        or dependency_declared(repo, "pytest")
    )
    if not pytest_contract:
        steps.append(("python_unittest", ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py", "-v"], 300))
        return steps

    if not dependency_declared(repo, "pytest"):
        steps.append(("python_test_dependency", ["__UNDECLARED_PYTEST__"], 0))
        return steps

    runner_packages = ["pytest"]
    if text_contains(tests, ("pytest.mark.asyncio", "pytest_asyncio")):
        if not dependency_declared(repo, "pytest-asyncio"):
            steps.append(("python_test_dependency", ["__UNDECLARED_PYTEST_ASYNCIO__"], 0))
            return steps
        runner_packages.append("pytest-asyncio")
    if text_contains(tests, ("pytest.mark.anyio",)):
        if not dependency_declared(repo, "anyio"):
            steps.append(("python_test_dependency", ["__UNDECLARED_ANYIO__"], 0))
            return steps
        runner_packages.append("anyio")

    steps.append(("python_test_dependencies", ["python", "-m", "pip", "install", *runner_packages], 300))
    steps.append(("python_pytest", ["python", "-m", "pytest", "-q"], 300))
    return steps


def node_steps(repo: Path) -> list[tuple[str, list[str], int]]:
    manifest = package_json(repo)
    if not manifest:
        return []
    scripts = manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
    dependency_maps = [manifest.get(name) for name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")]
    has_dependencies = any(isinstance(value, dict) and value for value in dependency_maps)
    steps: list[tuple[str, list[str], int]] = []
    if (repo / "pnpm-lock.yaml").is_file():
        steps.append(("node_dependencies", ["corepack", "pnpm", "install", "--frozen-lockfile"], 360))
        runner = ["corepack", "pnpm", "run"]
    elif (repo / "yarn.lock").is_file():
        steps.append(("node_dependencies", ["corepack", "yarn", "install", "--immutable"], 360))
        runner = ["corepack", "yarn", "run"]
    elif (repo / "package-lock.json").is_file() or (repo / "npm-shrinkwrap.json").is_file():
        steps.append(("node_dependencies", ["npm", "ci"], 360))
        runner = ["npm", "run"]
    elif has_dependencies:
        steps.append(("node_dependencies", ["__NO_LOCKFILE__"], 0))
        runner = ["npm", "run"]
    else:
        runner = ["npm", "run"]
    for script in ("test", "build"):
        if script in scripts:
            steps.append((f"node_{script}", [*runner, script], 360))
    return steps


def native_steps(repo: Path) -> list[tuple[str, list[str], int]]:
    steps: list[tuple[str, list[str], int]] = []
    steps.extend(python_steps(repo))
    steps.extend(node_steps(repo))
    if (repo / "go.mod").is_file():
        steps.append(("go_test", ["go", "test", "./..."], 360))
    if (repo / "Cargo.toml").is_file():
        steps.append(("cargo_test", ["cargo", "+stable", "test", "--all-targets"], 480))
    if (repo / "mvnw").is_file():
        steps.append(("maven_test", ["./mvnw", "-B", "test"], 480))
    elif (repo / "pom.xml").is_file() and shutil.which("mvn"):
        steps.append(("maven_test", ["mvn", "-B", "test"], 480))
    if (repo / "gradlew").is_file():
        steps.append(("gradle_test", ["./gradlew", "test", "--no-daemon"], 480))
    return steps


def classify(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "NO_SUPPORTED_EXECUTION_CONTRACT"
    incomplete = {
        "NOT_RUN_NO_LOCKFILE", "NOT_RUN_UNDECLARED_TEST_DEPENDENCY",
        "SKIPPED_DEPENDENCY_UNAVAILABLE",
    }
    material = [step for step in steps if step["status"] not in incomplete]
    if any(step["status"] in {"FAIL", "TIMEOUT", "TOOL_MISSING"} for step in material):
        return "BROKEN_EXECUTION_EVIDENCE"
    if any(step["status"] in incomplete for step in steps):
        return "INCOMPLETE_EXECUTION_EVIDENCE"
    test_names = {
        "python_unittest", "python_pytest", "go_test", "cargo_test",
        "maven_test", "gradle_test", "node_test",
    }
    if any(step["name"] in test_names for step in material):
        return "TESTED_EXECUTION_EVIDENCE"
    return "BUILDABLE_EXECUTION_EVIDENCE"


def probe_repo(record: dict[str, Any], out: Path) -> dict[str, Any]:
    full_name = record["full_name"]
    with tempfile.TemporaryDirectory(prefix="crystal-exec-") as tmp:
        root = Path(tmp)
        repo = root / "repo"
        try:
            clone = subprocess.run(
                ["git", "clone", "--quiet", "--depth=1", "--filter=blob:none", "--no-tags", "--branch", record.get("default_branch") or "main", f"https://github.com/{full_name}.git", str(repo)],
                text=True, capture_output=True, timeout=300,
            )
            if clone.returncode != 0:
                raise RuntimeError(f"clone_failed:{clone.returncode}:{clone.stderr[-500:]}")
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        except Exception as exc:
            result = {
                "schema": "glaciereq.crystallization.execution-probe.v3",
                "repository": full_name,
                "repository_id": record["id"],
                "status": "CLONE_ERROR",
                "completion_claim_allowed": False,
                "error": f"{type(exc).__name__}:{exc}",
                "steps": [],
            }
            result["evidence_digest"] = digest(result)
            return result

        env = clean_env(str(root / "home"))
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        if (repo / "src").is_dir():
            env["PYTHONPATH"] = str(repo / "src")
        specs = native_steps(repo)
        executed: list[dict[str, Any]] = []
        dependency_failed = False
        for name, command, timeout_s in specs:
            marker = command[0] if len(command) == 1 and command[0].startswith("__") else None
            if marker:
                if marker == "__NO_LOCKFILE__":
                    status = "NOT_RUN_NO_LOCKFILE"
                    message = "Declared Node dependencies exist without a reproducible lockfile."
                else:
                    status = "NOT_RUN_UNDECLARED_TEST_DEPENDENCY"
                    message = f"Repository test sources require a runner/plugin that repository manifests do not declare: {marker}."
                executed.append(synthetic_step(name, status, message))
                dependency_failed = True
                continue
            if dependency_failed and (name.startswith("node_") or name.startswith("python_")) and "dependencies" not in name:
                executed.append(synthetic_step(name, "SKIPPED_DEPENDENCY_UNAVAILABLE", "Skipped because a required reproducible dependency contract is unavailable."))
                continue
            step = run_step(name, command, repo, env, timeout_s)
            executed.append(step)
            if (
                name.startswith("node_dependencies")
                or name.startswith("python_dependencies")
                or name in {"python_package_install", "python_test_dependencies"}
            ) and step["status"] != "PASS":
                dependency_failed = True

        result = {
            "schema": "glaciereq.crystallization.execution-probe.v3",
            "repository": full_name,
            "repository_id": record["id"],
            "head_sha": head,
            "status": classify(executed),
            "completion_claim_allowed": False,
            "steps": executed,
        }
        result["evidence_digest"] = digest(result)
        return result


def write_result(out: Path, result: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    path = out / (result["repository"].replace("/", "__") + ".json")
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def scan_shard(registry_path: Path, output: Path, shard_index: int, shard_count: int) -> int:
    registry = json.loads(registry_path.read_text())
    repos = [repo for repo in registry["repositories"] if not repo.get("fork") and not repo.get("archived") and not repo.get("disabled")]
    repos.sort(key=lambda repo: repo["full_name"].lower())
    assigned = [repo for index, repo in enumerate(repos) if index % shard_count == shard_index]
    results = []
    for index, repo in enumerate(assigned, start=1):
        print(f"[{shard_index}:{index}/{len(assigned)}] {repo['full_name']}", flush=True)
        result = probe_repo(repo, output)
        write_result(output, result)
        results.append(result)
    summary = {
        "schema": "glaciereq.crystallization.execution-shard.v3",
        "shard_index": shard_index,
        "shard_count": shard_count,
        "repositories": len(results),
        "status_counts": dict(sorted(__import__("collections").Counter(row["status"] for row in results).items())),
    }
    summary["summary_digest"] = digest(summary)
    (output / f"_shard_{shard_index}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


def merge(shards: Path, output: Path, expected: int) -> int:
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(shards.rglob("GlacierEQ__*.json"))
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for source in files:
        data = json.loads(source.read_text())
        repo = data["repository"]
        if repo in seen:
            raise RuntimeError(f"duplicate_execution_evidence:{repo}")
        seen.add(repo)
        shutil.copyfile(source, output / source.name)
        status_counts[data["status"]] = status_counts.get(data["status"], 0) + 1
        rows.append({
            "repository": repo,
            "head_sha": data.get("head_sha"),
            "status": data["status"],
            "failed_steps": [step["name"] for step in data.get("steps", []) if step["status"] in {"FAIL", "TIMEOUT", "TOOL_MISSING"}],
            "incomplete_steps": [step["name"] for step in data.get("steps", []) if step["status"].startswith("NOT_RUN_") or step["status"].startswith("SKIPPED_")],
            "evidence_digest": data["evidence_digest"],
        })
    rows.sort(key=lambda row: row["repository"].lower())
    index = {
        "schema": "glaciereq.crystallization.public-execution-index.v3",
        "scope": "ACTIVE_NONFORK_NONARCHIVED_PUBLIC_ORIGINALS",
        "repository_count": len(rows),
        "expected_repository_count": expected,
        "coverage_complete": len(rows) == expected,
        "completion_claim_allowed": False,
        "status_counts": dict(sorted(status_counts.items())),
        "repositories": rows,
    }
    index["index_digest"] = digest(index)
    (output.parent / "public-execution-index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return 0 if len(rows) == expected else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="crystallization/public-estate-registry.json")
    parser.add_argument("--output-dir", default="crystallization/execution-index")
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int, default=16)
    parser.add_argument("--merge-dir")
    parser.add_argument("--expected-repos", type=int, default=160)
    args = parser.parse_args()
    if args.merge_dir:
        return merge(Path(args.merge_dir), Path(args.output_dir), args.expected_repos)
    if args.shard_index is None or not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("valid --shard-index required")
    return scan_shard(Path(args.registry), Path(args.output_dir), args.shard_index, args.shard_count)


if __name__ == "__main__":
    raise SystemExit(main())
