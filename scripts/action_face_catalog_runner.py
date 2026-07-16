#!/usr/bin/env python3
"""Execute migrated action-face adapters and delegate established adapters unchanged."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import apex_catalog_runner as catalog

CHECKOUT_PIN = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
SENSITIVE_ENV = {"APEX_CONTROL_TOKEN", "APEX_PRIVATE_READ_TOKEN", "GH_PAT"}


def executable_available(executable: str) -> bool:
    path = Path(executable)
    if path.is_absolute():
        return path.exists() and path.is_file()
    return shutil.which(executable) is not None


def run_sequence(plan: dict, workspace: Path, result_path: Path, commands: list[list[str]], timeout: int = 1800) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    steps: list[dict] = []
    failed = False
    env = os.environ.copy()
    env.update({"CI": "true", "NPM_CONFIG_FUND": "false", "NPM_CONFIG_AUDIT": "false"})

    for command in commands:
        executable = command[0]
        if not executable_available(executable):
            steps.append({"command": command, "status": "blocked", "reason": f"{executable} is unavailable"})
            failed = True
            break
        try:
            proc = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
                env=env,
            )
            output = proc.stdout[-100_000:]
            steps.append({
                "command": command,
                "exit_code": proc.returncode,
                "status": "completed" if proc.returncode == 0 else "failed",
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "output_tail": output[-24_000:],
            })
            if proc.returncode != 0:
                failed = True
                break
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            steps.append({
                "command": command,
                "status": "failed",
                "reason": f"timeout after {timeout} seconds",
                "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                "output_tail": output[-24_000:],
            })
            failed = True
            break
        except OSError as exc:
            steps.append({
                "command": command,
                "status": "failed",
                "reason": f"process start failed: {type(exc).__name__}: {exc}",
            })
            failed = True
            break

    return catalog.write_result(
        plan,
        result_path,
        "failed" if failed else "completed",
        steps=steps,
    )


def action_face_selftest(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail[:500]})

    leaked = sorted(key for key in SENSITIVE_ENV if os.environ.get(key))
    record("workload-secret-isolation", not leaked, "no protected token names are populated" if not leaked else f"unexpected variables: {', '.join(leaked)}")

    script_files = sorted((workspace / "scripts").glob("*.py"))
    syntax_failures: list[str] = []
    for path in script_files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            syntax_failures.append(f"{path.name}:{exc.lineno}")
    record("python-syntax", bool(script_files) and not syntax_failures, ", ".join(syntax_failures) or f"{len(script_files)} scripts compiled")

    json_failures: list[str] = []
    json_files = sorted((workspace / "config").glob("*.json"))
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            json_failures.append(f"{path.name}:{type(exc).__name__}")
    record("json-contracts", bool(json_files) and not json_failures, ", ".join(json_failures) or f"{len(json_files)} JSON files parsed")

    workflow_path = workspace / ".github" / "workflows" / "apex-pillar-runner.yml"
    workflow = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    required_fragments = [
        "name: APEX Public Action Face",
        "runs-on: ubuntu-latest",
        "scripts/action_face_guard.py",
        "scripts/action_face_authorize.py",
        "scripts/action_face_control_plane_guard.py",
        CHECKOUT_PIN,
    ]
    forbidden_fragments = [
        "runs-on: self-hosted",
        "secrets.GH_PAT",
        "actions/github-script@",
        "actions/checkout@v",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in workflow]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in workflow]
    record("workflow-invariants", bool(workflow) and not missing and not forbidden, f"missing={missing}; forbidden={forbidden}")

    catalog_entries: list[dict] = []
    for name in ("pillar-actions.json", "action-face-actions.json"):
        path = workspace / "config" / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            catalog_entries.extend(data.get("actions", []))
    keys = [(item.get("pillar"), item.get("action")) for item in catalog_entries]
    targets = [str(item.get("target_repo", "")) for item in catalog_entries]
    catalog_ok = bool(keys) and len(keys) == len(set(keys)) and all(target.startswith("GlacierEQ/") for target in targets)
    record("catalog-uniqueness", catalog_ok, f"{len(keys)} catalog actions checked")

    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        valid_event = temp_path / "valid.json"
        valid_event.write_text(json.dumps({"action": "action-face-canary", "client_payload": {"job_id": "canary-20260716-001", "source_ref": "main"}}), encoding="utf-8")
        invalid_event = temp_path / "invalid.json"
        invalid_event.write_text(json.dumps({"action": "action-face-canary", "client_payload": {"job_id": "canary-20260716-002", "source_ref": "main", "unexpected": "blocked"}}), encoding="utf-8")

        valid = subprocess.run(
            [sys.executable, "scripts/action_face_plan.py", "--event", str(valid_event)],
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        invalid = subprocess.run(
            [sys.executable, "scripts/action_face_plan.py", "--event", str(invalid_event)],
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        record("planner-positive-negative", valid.returncode == 0 and invalid.returncode != 0, f"valid={valid.returncode}; invalid={invalid.returncode}")

        issue_event = temp_path / "issue.json"
        issue_event.write_text(json.dumps({"issue": {"user": {"login": "GlacierEQ"}, "author_association": "OWNER"}}), encoding="utf-8")
        auth_env = {
            **os.environ,
            "GITHUB_REPOSITORY": "GlacierEQ/public-actions-runner-host",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_ACTOR": "GlacierEQ",
            "GITHUB_EVENT_PATH": str(issue_event),
        }
        authorized = subprocess.run(
            [sys.executable, "scripts/action_face_authorize.py"],
            cwd=workspace,
            env=auth_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        issue_event.write_text(json.dumps({"issue": {"user": {"login": "intruder"}, "author_association": "NONE"}}), encoding="utf-8")
        unauthorized = subprocess.run(
            [sys.executable, "scripts/action_face_authorize.py"],
            cwd=workspace,
            env={**auth_env, "GITHUB_ACTOR": "intruder"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        record("authorization-positive-negative", authorized.returncode == 0 and unauthorized.returncode != 0, f"authorized={authorized.returncode}; unauthorized={unauthorized.returncode}")

    failed = [check for check in checks if check["status"] != "pass"]
    return catalog.write_result(
        plan,
        result_path,
        "failed" if failed else "completed",
        checks=checks,
        check_count=len(checks),
        failed_count=len(failed),
    )


def node_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    package_path = workspace / "package.json"
    if not package_path.exists():
        return catalog.write_result(plan, result_path.resolve(), "blocked", reason="package.json was not found")

    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    install_commands: list[list[str]] = []
    check_commands: list[list[str]] = []

    if (workspace / "package-lock.json").exists():
        install_commands.append(["npm", "ci"])
        runner = "npm"
    elif (workspace / "pnpm-lock.yaml").exists():
        install_commands.extend([["corepack", "enable"], ["pnpm", "install", "--frozen-lockfile"]])
        runner = "pnpm"
    elif (workspace / "yarn.lock").exists():
        install_commands.extend([["corepack", "enable"], ["yarn", "install", "--frozen-lockfile"]])
        runner = "yarn"
    else:
        install_commands.append(["npm", "install"])
        runner = "npm"

    def script_command(name: str) -> list[str]:
        if runner == "npm":
            return ["npm", "run", name]
        if runner == "pnpm":
            return ["pnpm", "run", name]
        return ["yarn", name]

    if "typecheck" in scripts:
        check_commands.append(script_command("typecheck"))
    elif (workspace / "tsconfig.json").exists():
        local_tsc = workspace / "node_modules" / ".bin" / "tsc"
        check_commands.append([str(local_tsc), "--noEmit"])
    if "lint" in scripts:
        check_commands.append(script_command("lint"))
    if "test" in scripts:
        check_commands.append(script_command("test"))
    if "build" in scripts:
        check_commands.append(script_command("build"))

    if not check_commands:
        return catalog.write_result(plan, result_path.resolve(), "blocked", reason="no CI scripts or TypeScript config were found")
    return run_sequence(plan, workspace, result_path, install_commands + check_commands)


def python_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    if not any((workspace / name).exists() for name in ("pyproject.toml", "requirements.txt", "setup.py")):
        return catalog.write_result(plan, result_path.resolve(), "blocked", reason="no Python project manifest was found")

    commands: list[list[str]] = [[sys.executable, "-m", "pip", "install", "--upgrade", "pip"]]
    if (workspace / "requirements.txt").exists():
        commands.append([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    elif (workspace / "pyproject.toml").exists() or (workspace / "setup.py").exists():
        commands.append([sys.executable, "-m", "pip", "install", "-e", "."])
    commands.append([sys.executable, "-m", "pip", "install", "ruff", "pytest"])
    commands.append([sys.executable, "-m", "ruff", "check", "."])
    if (workspace / "tests").exists() or (workspace / "pytest.ini").exists():
        commands.append([sys.executable, "-m", "pytest", "-q"])
    return run_sequence(plan, workspace, result_path, commands)


def apex_verify(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    script = workspace / "apex-mastermind" / "scripts" / "verify" / "apex-verify.py"
    if not script.exists():
        return catalog.write_result(plan, result_path, "blocked", reason="APEX verification entrypoint was not found in the workload")

    report_path = result_path.parent / f"{plan['job_id']}.apex-verification.json"
    command = [sys.executable, str(script), str(workspace), "--out", str(report_path)]
    try:
        proc = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3600,
            check=False,
            env={**os.environ, "CI": "true"},
        )
        output = proc.stdout[-100_000:]
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return catalog.write_result(plan, result_path, "failed", command=command, reason="APEX verification timed out after 3600 seconds", output_sha256=hashlib.sha256(output.encode()).hexdigest(), output_tail=output[-32_000:])
    except OSError as exc:
        return catalog.write_result(plan, result_path, "failed", command=command, reason=f"APEX verification failed to start: {type(exc).__name__}: {exc}")

    report = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            report = {"release_state": "Block", "reason": "verification report was not valid JSON"}

    release_state = report.get("release_state") if isinstance(report, dict) else None
    status = "completed" if exit_code == 0 and release_state != "Block" else "failed"
    return catalog.write_result(plan, result_path, status, command=command, exit_code=exit_code, release_state=release_state, verification_report=report, output_sha256=hashlib.sha256(output.encode()).hexdigest(), output_tail=output[-32_000:])


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: action_face_catalog_runner.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    adapter = plan.get("adapter")
    if adapter == "action-face-selftest":
        return action_face_selftest(plan, workspace, result_path)
    if adapter == "apex-verify":
        return apex_verify(plan, workspace, result_path)
    if adapter == "node-ci":
        return node_ci(plan, workspace, result_path)
    if adapter == "python-ci":
        return python_ci(plan, workspace, result_path)
    return subprocess.call([sys.executable, "scripts/apex_catalog_runner.py", str(plan_path), str(workspace), str(result_path)])


if __name__ == "__main__":
    raise SystemExit(main())
