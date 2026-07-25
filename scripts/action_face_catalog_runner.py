#!/usr/bin/env python3
"""Execute migrated action-face adapters and delegate established adapters unchanged."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import apex_catalog_runner as catalog
from action_face_selftest import run as run_selftest
from master_strand_extinction import ExtinctionError, run as run_master_strand

SENSITIVE_ENV = {
    "APEX_BRANCH_WRITE_TOKEN",
    "APEX_CONTROL_TOKEN",
    "APEX_PRIVATE_READ_TOKEN",
    "GH_PAT",
    "GITHUB_TOKEN",
}


def executable_available(executable: str) -> bool:
    path = Path(executable)
    if path.is_absolute():
        return path.exists() and path.is_file()
    return shutil.which(executable) is not None


def isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in SENSITIVE_ENV:
        env.pop(key, None)
    env.update({
        "CI": "true",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_AUDIT": "false",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    })
    return env


def run_sequence(plan: dict, workspace: Path, result_path: Path, commands: list[list[str]], timeout: int = 1800) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    steps: list[dict] = []
    failed = False
    env = isolated_env()

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

    return catalog.write_result(plan, result_path, "failed" if failed else "completed", steps=steps)


def node_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    package_path = workspace / "package.json"
    if not package_path.exists():
        return catalog.write_result(plan, result_path, "blocked", reason="package.json was not found")

    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return catalog.write_result(plan, result_path, "blocked", reason=f"package.json is invalid at line {exc.lineno}")
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    install_commands: list[list[str]] = []
    check_commands: list[list[str]] = []

    if (workspace / "package-lock.json").exists():
        install_commands.append(["npm", "ci"])
        runner = "npm"
    elif (workspace / "pnpm-lock.yaml").exists():
        install_commands.append(["corepack", "pnpm", "install", "--frozen-lockfile"])
        runner = "pnpm"
    elif (workspace / "yarn.lock").exists():
        immutable_flag = "--immutable" if (workspace / ".yarnrc.yml").exists() else "--frozen-lockfile"
        install_commands.append(["corepack", "yarn", "install", immutable_flag])
        runner = "yarn"
    else:
        return catalog.write_result(plan, result_path, "blocked", reason="a package manager lockfile is required for reproducible Node CI")

    def script_command(name: str) -> list[str]:
        if runner == "npm":
            return ["npm", "run", name]
        return ["corepack", runner, "run", name]

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
        return catalog.write_result(plan, result_path, "blocked", reason="no CI scripts or TypeScript config were found")
    return run_sequence(plan, workspace, result_path, install_commands + check_commands)


def python_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    if not any((workspace / name).exists() for name in ("pyproject.toml", "requirements.txt", "setup.py")):
        return catalog.write_result(plan, result_path, "blocked", reason="no Python project manifest was found")

    venv = result_path.parent / f"venv-{plan['job_id']}"
    venv_python = venv / "bin" / "python"
    commands: list[list[str]] = [[sys.executable, "-m", "venv", str(venv)]]
    commands.append([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
    if (workspace / "requirements.txt").exists():
        commands.append([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"])
    elif (workspace / "pyproject.toml").exists() or (workspace / "setup.py").exists():
        commands.append([str(venv_python), "-m", "pip", "install", "-e", "."])
    commands.append([str(venv_python), "-m", "pip", "install", "ruff", "pytest"])
    commands.append([str(venv_python), "-m", "pip", "check"])
    commands.append([str(venv_python), "-m", "ruff", "check", "."])
    if (workspace / "tests").exists() or (workspace / "pytest.ini").exists():
        commands.append([str(venv_python), "-m", "pytest", "-q"])
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
            env=isolated_env(),
        )
        output = proc.stdout[-100_000:]
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            command=command,
            reason="APEX verification timed out after 3600 seconds",
            output_sha256=hashlib.sha256(output.encode()).hexdigest(),
            output_tail=output[-32_000:],
        )
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
    return catalog.write_result(
        plan,
        result_path,
        status,
        command=command,
        exit_code=exit_code,
        release_state=release_state,
        verification_report=report,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        output_tail=output[-32_000:],
    )


def master_strand(plan: dict, result_path: Path, mode: str) -> int:
    try:
        report = run_master_strand(
            owner="GlacierEQ",
            mode=mode,
            job_id=str(plan["job_id"]),
            approval_id=str(plan.get("approval_id") or "") or None,
        )
    except ExtinctionError as exc:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            exact_blocker=str(exc),
            master_strand_mode=mode,
        )
    status = "completed" if report.get("status") == "completed" else "failed"
    return catalog.write_result(
        plan,
        result_path,
        status,
        master_strand_mode=mode,
        master_strand=report,
    )


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: action_face_catalog_runner.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    adapter = plan.get("adapter")
    if adapter == "action-face-selftest":
        return run_selftest(plan, workspace, result_path)
    if adapter == "apex-verify":
        return apex_verify(plan, workspace, result_path)
    if adapter == "node-ci":
        return node_ci(plan, workspace, result_path)
    if adapter == "python-ci":
        return python_ci(plan, workspace, result_path)
    if adapter == "master-strand-inventory":
        return master_strand(plan, result_path, "inventory")
    if adapter == "master-strand-extinction":
        return master_strand(plan, result_path, "apply")
    return subprocess.call([sys.executable, "scripts/apex_catalog_runner.py", str(plan_path), str(workspace), str(result_path)], env=isolated_env())


if __name__ == "__main__":
    raise SystemExit(main())
