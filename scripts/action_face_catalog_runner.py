#!/usr/bin/env python3
"""Execute migrated action-face adapters and delegate established adapters unchanged."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import apex_catalog_runner as catalog
from action_face_selftest import run as run_selftest
from domains.code.adapters.fileboss_security_validate import (
    run as run_fileboss_security_validate,
)
from domains.code.adapters.tool_system_validate import (
    run as run_tool_system_validate,
)
from master_strand_extinction import ExtinctionError, run as run_master_strand
from monolith_evolution_adapter import run as run_monolith_evolution
from monolith_ip_governance_adapter import run as run_monolith_ip_governance
from workload_isolation import WorkloadIsolationError, build_environment


def executable_available(executable: str) -> bool:
    path = Path(executable)
    if path.is_absolute():
        return path.exists() and path.is_file()
    return shutil.which(executable) is not None


def isolated_env(
    result_path: Path,
    job_id: str,
    *,
    include_resolved_sha: bool = False,
) -> dict[str, str]:
    extra = None
    if include_resolved_sha:
        extra = {
            "APEX_RESOLVED_SOURCE_SHA": os.environ.get(
                "APEX_RESOLVED_SOURCE_SHA", ""
            )
        }
    return build_environment(result_path, job_id, extra=extra)


def run_sequence(
    plan: dict,
    workspace: Path,
    result_path: Path,
    commands: list[list[str]],
    timeout: int = 1800,
) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    steps: list[dict] = []
    failed = False
    try:
        env = isolated_env(result_path, str(plan["job_id"]))
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"workload isolation failed: {error}",
        )

    for command in commands:
        executable = command[0]
        if not executable_available(executable):
            steps.append(
                {
                    "command": command,
                    "status": "blocked",
                    "reason": f"{executable} is unavailable",
                }
            )
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
                shell=False,
                env=env,
            )
            output = (proc.stdout or "")[-100_000:]
            steps.append(
                {
                    "command": command,
                    "exit_code": proc.returncode,
                    "status": "completed" if proc.returncode == 0 else "failed",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            if proc.returncode != 0:
                failed = True
                break
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": f"timeout after {timeout} seconds",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            failed = True
            break
        except OSError as exc:
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": (
                        f"process start failed: {type(exc).__name__}: {exc}"
                    ),
                }
            )
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
    result_path = result_path.resolve()
    package_path = workspace / "package.json"
    if not package_path.exists():
        return catalog.write_result(
            plan, result_path, "blocked", reason="package.json was not found"
        )

    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"package.json is invalid at line {exc.lineno}",
        )
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    install_commands: list[list[str]] = []
    check_commands: list[list[str]] = []

    if (workspace / "package-lock.json").exists():
        install_commands.append(["npm", "ci"])
        runner = "npm"
    elif (workspace / "pnpm-lock.yaml").exists():
        install_commands.append(
            ["corepack", "pnpm", "install", "--frozen-lockfile"]
        )
        runner = "pnpm"
    elif (workspace / "yarn.lock").exists():
        immutable_flag = (
            "--immutable"
            if (workspace / ".yarnrc.yml").exists()
            else "--frozen-lockfile"
        )
        install_commands.append(
            ["corepack", "yarn", "install", immutable_flag]
        )
        runner = "yarn"
    else:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="a package manager lockfile is required for reproducible Node CI",
        )

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
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="no CI scripts or TypeScript config were found",
        )
    return run_sequence(
        plan, workspace, result_path, install_commands + check_commands
    )


def python_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    if not any(
        (workspace / name).exists()
        for name in ("pyproject.toml", "requirements.txt", "setup.py")
    ):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="no Python project manifest was found",
        )

    venv = result_path.parent / f"venv-{plan['job_id']}"
    venv_python = venv / "bin" / "python"
    commands: list[list[str]] = [
        [sys.executable, "-m", "venv", str(venv)]
    ]
    commands.append(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"]
    )
    if (workspace / "requirements.txt").exists():
        commands.append(
            [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"]
        )
    elif (workspace / "pyproject.toml").exists() or (
        workspace / "setup.py"
    ).exists():
        commands.append(
            [str(venv_python), "-m", "pip", "install", "-e", "."]
        )
    commands.append(
        [str(venv_python), "-m", "pip", "install", "ruff", "pytest"]
    )
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
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="APEX verification entrypoint was not found in the workload",
        )

    report_path = result_path.parent / f"{plan['job_id']}.apex-verification.json"
    command = [sys.executable, str(script), str(workspace), "--out", str(report_path)]
    try:
        env = isolated_env(result_path, str(plan["job_id"]))
        proc = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3600,
            check=False,
            shell=False,
            env=env,
        )
        output = (proc.stdout or "")[-100_000:]
        exit_code = proc.returncode
    except WorkloadIsolationError as exc:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            command=command,
            reason=f"workload isolation failed: {exc}",
        )
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
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            command=command,
            reason=(
                f"APEX verification failed to start: {type(exc).__name__}: {exc}"
            ),
        )

    report = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            report = {
                "release_state": "Block",
                "reason": "verification report was not valid JSON",
            }

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


def akos_echo_policy_ci(plan: dict, workspace: Path, result_path: Path) -> int:
    """Run the exact AKOS-Echo policy gate from the nested FILEBOSS project."""
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    project = workspace / "genius" / "pro-code"
    policy = (
        project
        / "smithery_control_plane"
        / "config"
        / "akos_connector_policy.json"
    )
    tests = [
        project / "tests" / "test_connector_policy.py",
        project / "tests" / "test_connector_gateway.py",
        project / "tests" / "test_connector_entrypoint_integration.py",
    ]

    if not project.is_dir():
        return catalog.write_result(
            plan, result_path, "blocked", reason="genius/pro-code was not found"
        )
    missing = [
        path.relative_to(workspace).as_posix()
        for path in [policy, *tests]
        if not path.is_file()
    ]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"required AKOS gate files are missing: {', '.join(missing)}",
        )

    digest = os.environ.get("AKOS_POLICY_SHA256", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="AKOS_POLICY_SHA256 is unavailable or invalid",
        )

    venv = result_path.parent / f"venv-{plan['job_id']}"
    python = venv / "bin" / "python"
    commands = [
        [sys.executable, "-m", "venv", str(venv)],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "pytest",
            "pytest-asyncio",
        ],
        [
            str(python),
            "-m",
            "smithery_control_plane.runtime.connector_policy",
            "--validate",
            "smithery_control_plane/config/akos_connector_policy.json",
        ],
        [
            str(python),
            "-m",
            "pytest",
            "tests/test_connector_policy.py",
            "tests/test_connector_gateway.py",
            "tests/test_connector_entrypoint_integration.py",
            "-q",
        ],
    ]
    return run_sequence(plan, project, result_path, commands)


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
        raise SystemExit(
            "usage: action_face_catalog_runner.py PLAN WORKSPACE RESULT"
        )
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
    if adapter == "akos-echo-policy-ci":
        return akos_echo_policy_ci(plan, workspace, result_path)
    if adapter == "tool-system-validate":
        return run_tool_system_validate(plan, workspace, result_path)
    if adapter == "monolith-evolution":
        return run_monolith_evolution(plan, workspace, result_path)
    if adapter == "monolith-ip-governance":
        return run_monolith_ip_governance(plan, workspace, result_path)
    if adapter == "fileboss_security_validate":
        return run_fileboss_security_validate(plan, workspace, result_path)
    if adapter == "master-strand-inventory":
        return master_strand(plan, result_path, "inventory")
    if adapter == "master-strand-extinction":
        return master_strand(plan, result_path, "apply")

    try:
        env = isolated_env(
            result_path,
            str(plan["job_id"]),
            include_resolved_sha=True,
        )
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"workload isolation failed: {error}",
        )
    return subprocess.call(
        [
            sys.executable,
            "scripts/apex_catalog_runner.py",
            str(plan_path),
            str(workspace),
            str(result_path),
        ],
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
