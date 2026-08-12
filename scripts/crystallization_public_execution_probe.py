#!/usr/bin/env python3
"""Run secretless reproducible execution probes across public original repos.

The probe is evidence, not a universal build system. It executes only commands
strongly implied by repository-native manifests and test sources. Unsupported or
unreproducible contracts remain explicitly incomplete instead of being declared
healthy or falsely broken by an invented test runner.
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
from pathlib import Path
from typing import Any

ENV_ALLOW = {
    "PATH", "LANG", "LC_ALL", "TZ", "TERM", "RUNNER_OS", "RUNNER_ARCH",
    "ImageOS", "ImageVersion", "HOME", "CARGO_HOME", "RUSTUP_HOME",
    "GOROOT", "GOPATH", "JAVA_HOME", "JAVA_HOME_17_X64", "JAVA_HOME_21_X64",
}
TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", re.I)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def clean_env(home: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW}
    # Keep toolchain homes injected by setup actions. Only isolate HOME itself.
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
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        status = "TIMEOUT"
        code = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    except FileNotFoundError as exc:
        status = "TOOL_MISSING"
        code = None
        stdout = ""
        stderr = str(exc)
    elapsed = round(time.monotonic() - started, 3)
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
        "elapsed_s": elapsed,
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
        raw = json.loads((repo / "package.json").read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def python_test_files(repo: Path) -> list[Path]:
    files = []
    for path in repo.rglob("*.py"):
        try:
            rel = path.relative_to(repo).as_posix()
        except ValueError:
            continue
        if TEST_FILE_RE.search(rel):
            files.append(path)
    return sorted(files)


def text_contains(paths: list[Path], needles: tuple[str, ...]) -> bool:
    for path in paths:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in needles):
            return True
    return False


def declared_pytest(repo: Path) -> bool:
    manifests = [
        repo / "requirements.txt", repo / "requirements-dev.txt",
        repo / "requirements-test.txt", repo / "pyproject.toml",
        repo / "pytest.ini", repo / "tox.ini",
    ]
    for path in manifests:
        if path.is_file():
            try:
                if "pytest" in path.read_text(errors="replace").lower():
                    return True
            except OSError:
                pass
    workflows = list((repo / ".github" / "workflows").glob("*.yml")) + list((repo / ".github" / "workflows").glob("*.yaml"))
    for path in workflows:
        try:
            text = path.read_text(errors="replace").lower()
        except OSError:
            continue
        if "pytest" in text and "pip install" in text:
            return True
    return False


def python_steps(repo: Path) -> list[tuple[str, list[str], int]]:
    py_files = list(repo.rglob("*.py"))
    if not py_files:
        return []
    steps: list[tuple[str, list[str], int]] = [
        ("python_compile", ["python", "-m", "compileall", "-q", "."], 180),
    ]
    tests = python_test_files(repo)
    if not tests:
        return steps

    requirements = [name for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt") if (repo / name).is_file()]
    for name in requirements:
        steps.append((f"python_dependencies:{name}", ["python", "-m", "pip", "install", "-r", name], 300))
    if not requirements and (repo / "pyproject.toml").is_file():
        steps.append(("python_package_install", ["python", "-m", "pip", "install", "-e", "."], 300))

    pytest_source = text_contains(tests, ("import pytest", "from pytest", "pytest."))
    pytest_contract = pytest_source or (repo / "pytest.ini").is_file() or "pytest" in (repo / "pyproject.toml").read_text(errors="replace").lower() if (repo / "pyproject.toml").is_file() else pytest_source or (repo / "pytest.ini").is_file()
    if pytest_contract:
        if declared_pytest(repo):
            # A workflow-only declaration still needs the runner installed here.
            if not requirements and not (repo / "pyproject.toml").is_file():
                steps.append(("python_test_dependencies", ["python", "-m", "pip", "install", "pytest"], 300))
            steps.append(("python_pytest", ["python", "-m", "pytest", "-q"], 300))
        else:
            steps.append(("python_test_dependency", ["__UNDECLARED_PYTEST__"], 0))
    else:
        # stdlib unittest is the correct zero-dependency default, not pytest-by-fiat.
        steps.append(("python_unittest", ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py", "-v"], 300))
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
        # A dependency-free package does not need a lockfile merely to execute scripts.
        runner = ["npm", "run"]
    for script in ("test", "build"):
        if script in scripts:
            steps.append((f"node_{script}", [*runner, script], 360))
    return steps


def native_steps(repo: Path) -> list[tuple[str, list[str], int]]:
    steps = []
    steps.extend(python_steps(repo))
    steps.extend(node_steps(repo))
    if (repo / "go.mod").is_file():
        steps.append(("go_test", ["go", "test", "./..."], 360))
    if (repo / "Cargo.toml").is_file():
        steps.append(("cargo_test", ["cargo", "test", "--all-targets"], 480))
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
    incomplete_statuses = {
        "NOT_RUN_NO_LOCKFILE", "NOT_RUN_UNDECLARED_TEST_DEPENDENCY",
        "SKIPPED_DEPENDENCY_UNAVAILABLE",
    }
    material = [s for s in steps if s["status"] not in incomplete_statuses]
    if any(s["status"] in {"FAIL", "TIMEOUT", "TOOL_MISSING"} for s in material):
        return "BROKEN_EXECUTION_EVIDENCE"
    if any(s["status"] in incomplete_statuses for s in steps):
        return "INCOMPLETE_EXECUTION_EVIDENCE"
    test_names = {"python_unittest", "python_pytest", "go_test", "cargo_test", "maven_test", "gradle_test", "node_test"}
    if any(s["name"] in test_names for s in material):
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
                "schema": "glaciereq.crystallization.execution-probe.v2",
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
            if command == ["__NO_LOCKFILE__"]:
                executed.append(synthetic_step(name, "NOT_RUN_NO_LOCKFILE", "Reproducible Node dependency installation refused because declared dependencies exist without a lockfile."))
                dependency_failed = True
                continue
            if command == ["__UNDECLARED_PYTEST__"]:
                executed.append(synthetic_step(name, "NOT_RUN_UNDECLARED_TEST_DEPENDENCY", "Tests require pytest but the repository does not declare pytest in package metadata, requirements, pytest config, or its native CI install contract."))
                dependency_failed = True
                continue
            if dependency_failed and (name.startswith("node_") or name.startswith("python_")) and "dependencies" not in name:
                executed.append(synthetic_step(name, "SKIPPED_DEPENDENCY_UNAVAILABLE", "Skipped because a required reproducible dependency contract is unavailable."))
                continue
            step = run_step(name, command, repo, env, timeout_s)
            executed.append(step)
            if (name.startswith("node_dependencies") or name.startswith("python_dependencies") or name in {"python_package_install", "python_test_dependencies"}) and step["status"] != "PASS":
                dependency_failed = True
        result = {
            "schema": "glaciereq.crystallization.execution-probe.v2",
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
    file = out / (result["repository"].replace("/", "__") + ".json")
    file.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def scan_shard(registry_path: Path, output: Path, shard_index: int, shard_count: int) -> int:
    registry = json.loads(registry_path.read_text())
    repos = [r for r in registry["repositories"] if not r.get("fork") and not r.get("archived") and not r.get("disabled")]
    repos.sort(key=lambda r: r["full_name"].lower())
    assigned = [r for i, r in enumerate(repos) if i % shard_count == shard_index]
    results = []
    for i, repo in enumerate(assigned, start=1):
        print(f"[{shard_index}:{i}/{len(assigned)}] {repo['full_name']}", flush=True)
        result = probe_repo(repo, output)
        write_result(output, result)
        results.append(result)
    summary = {
        "schema": "glaciereq.crystallization.execution-shard.v2",
        "shard_index": shard_index,
        "shard_count": shard_count,
        "repositories": len(results),
        "status_counts": dict(sorted(__import__('collections').Counter(r["status"] for r in results).items())),
    }
    summary["summary_digest"] = digest(summary)
    (output / f"_shard_{shard_index}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


def merge(shards: Path, output: Path, expected: int) -> int:
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(shards.rglob("GlacierEQ__*.json"))
    seen = set()
    rows = []
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
            "failed_steps": [s["name"] for s in data.get("steps", []) if s["status"] in {"FAIL", "TIMEOUT", "TOOL_MISSING"}],
            "incomplete_steps": [s["name"] for s in data.get("steps", []) if s["status"].startswith("NOT_RUN_") or s["status"].startswith("SKIPPED_")],
            "evidence_digest": data["evidence_digest"],
        })
    rows.sort(key=lambda r: r["repository"].lower())
    index = {
        "schema": "glaciereq.crystallization.public-execution-index.v2",
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
