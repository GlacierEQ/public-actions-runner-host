#!/usr/bin/env python3
"""Execute migrated action-face adapters and delegate established adapters unchanged."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import apex_catalog_runner as catalog


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
    if plan.get("adapter") == "apex-verify":
        return apex_verify(plan, workspace, result_path)
    return subprocess.call(
        [sys.executable, "scripts/apex_catalog_runner.py", str(plan_path), str(workspace), str(result_path)]
    )


if __name__ == "__main__":
    raise SystemExit(main())
