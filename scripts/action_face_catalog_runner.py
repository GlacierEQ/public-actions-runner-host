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


def run_sequence(plan: dict, workspace: Path, result_path: Path, commands: list[list[str]], timeout: int = 1800) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    steps: list[dict] = []
    failed = False
    env = os.environ.copy()
    env.update({"CI": "true", "NPM_CONFIG_FUND": "false", "NPM_CONFIG_AUDIT": "false"})

    for command in commands:
        executable = command[0]
        if not Path(executable).is_absolute() and not shutil.which(executable):
            steps.append({"command": command, "status": "blocked", "reason": f"{executable} is unavailable"})
            failed = True
            break
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

    return catalog.write_result(
        plan,
        result_path,
        "failed" if failed else "completed",
        steps=steps,
    )


def node_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    package_path = workspace / "package.json"
    if not package_path.exists():
        return catalog.write_result(plan, result_path.resolve(), "blocked", reason="package.json was not found")

    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    commands: list[list[str]] = []

    if (workspace / "package-lock.json").exists():
        commands.append(["npm", "ci"])
        runner = "npm"
    elif (workspace / "pnpm-lock.yaml").exists():
        commands.extend([["corepack", "enable"], ["pnpm", "install", "--frozen-lockfile"]])
        runner = "pnpm"
    elif (workspace / "yarn.lock").exists():
        commands.extend([["corepack", "enable"], ["yarn", "install", "--frozen-lockfile"]])
        runner = "yarn"
    else:
        commands.append(["npm", "install"])
        runner = "npm"

    def script_command(name: str) -> list[str]:
        if runner == "npm":
            return ["npm", "run", name]
        if runner == "pnpm":
            return ["pnpm", "run", name]
        return ["yarn", name]

    if "typecheck" in scripts:
        commands.append(script_command("typecheck"))
    elif (workspace / "tsconfig.json").exists():
        local_tsc = workspace / "node_modules" / ".bin" / "tsc"
        commands.append([str(local_tsc), "--noEmit"])
    if "lint" in scripts:
        commands.append(script_command("lint"))
    if "test" in scripts:
        commands.append(script_command("test"))
    if "build" in scripts:
        commands.append(script_command("build"))

    if len(commands) == 1:
        return catalog.write_result(plan, result_path.resolve(), "blocked", reason="no CI scripts were found")
    return run_sequence(plan, workspace, result_path, commands)


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
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="APEX verification entrypoint was not found in the workload",
        )

    report_path = result_path.parent / f"{plan['job_id']}.apex-verification.json"
    command = [sys.executable, str(script), str(workspace), "--out", str(report_path)]
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
    report = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            report = {"release_state": "Block", "reason": "verification report was not valid JSON"}

    release_state = report.get("release_state") if isinstance(report, dict) else None
    status = "completed" if proc.returncode == 0 and release_state != "Block" else "failed"
    return catalog.write_result(
        plan,
        result_path,
        status,
        command=command,
        exit_code=proc.returncode,
        release_state=release_state,
        verification_report=report,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        output_tail=output[-32_000:],
    )


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: action_face_catalog_runner.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    adapter = plan.get("adapter")
    if adapter == "apex-verify":
        return apex_verify(plan, workspace, result_path)
    if adapter == "node-ci":
        return node_ci(plan, workspace, result_path)
    if adapter == "python-ci":
        return python_ci(plan, workspace, result_path)
    return subprocess.call(
        [sys.executable, "scripts/apex_catalog_runner.py", str(plan_path), str(workspace), str(result_path)]
    )


if __name__ == "__main__":
    raise SystemExit(main())
