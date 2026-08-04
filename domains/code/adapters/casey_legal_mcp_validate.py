"""Validate the private Casey legal MCP evidence-control core."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import apex_catalog_runner as catalog
from scripts.workload_isolation import (
    WorkloadIsolationError,
    attest_workspace,
    build_environment,
    command_contract_sha256,
)

EXPECTED_ACTION = "code.casey-legal-mcp.validate-v2"
EXPECTED_REPOSITORY = "GlacierEQ/casey-legal-mcp-server"
EXPECTED_ADAPTER = "casey_legal_mcp_validate"
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PATHS = (
    "src/policy.js",
    "src/index.js",
    "test/policy.test.js",
    "package.json",
    "README.md",
    "SECURITY.md",
    "automation/public-runner/evidence-control-v2.request.json",
)


def validate_plan(plan: dict) -> None:
    expected = {
        "pillar": "C",
        "action": EXPECTED_ACTION,
        "adapter": EXPECTED_ADAPTER,
        "task": "test",
        "source_repo": EXPECTED_REPOSITORY,
        "target_repo": EXPECTED_REPOSITORY,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise ValueError(f"{field} identity mismatch")


def commands(node: str) -> list[list[str]]:
    return [
        [node, "--version"],
        [node, "--check", "src/policy.js"],
        [node, "--check", "src/index.js"],
        [node, "--test", "test/policy.test.js"],
    ]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()
    try:
        validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA is unavailable or invalid",
        )
    if resolved_sha != plan.get("source_ref"):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA does not match requested source_ref",
        )

    missing = [relative for relative in REQUIRED_PATHS if not (workspace / relative).is_file()]
    if missing:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required Casey legal MCP files are missing: " + ", ".join(missing),
        )

    node = shutil.which("node")
    if not node:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="Node.js runtime is unavailable",
        )

    try:
        pre_attestation = attest_workspace(workspace, resolved_sha)
        env = build_environment(result_path, str(plan["job_id"]))
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"workload isolation failed before execution: {error}",
        )

    sequence = commands(node)
    steps: list[dict] = []
    status = "completed"
    node_major: int | None = None
    for index, command in enumerate(sequence):
        try:
            process = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=False,
                shell=False,
                env=env,
            )
            output = (process.stdout or "")[-100_000:]
            if index == 0 and process.returncode == 0:
                match = re.search(r"v(\d+)", output)
                node_major = int(match.group(1)) if match else None
                if node_major is None or node_major < 20:
                    process = subprocess.CompletedProcess(
                        process.args,
                        1,
                        output + "\nNode.js 20 or newer is required.\n",
                    )
                    output = process.stdout
            steps.append(
                {
                    "command": command,
                    "exit_code": process.returncode,
                    "status": "completed" if process.returncode == 0 else "failed",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            if process.returncode != 0:
                status = "failed"
                break
        except subprocess.TimeoutExpired as error:
            output = error.stdout if isinstance(error.stdout, str) else ""
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": "timeout after 900 seconds",
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                    "output_tail": output[-24_000:],
                }
            )
            status = "failed"
            break
        except OSError as error:
            steps.append(
                {
                    "command": command,
                    "status": "failed",
                    "reason": f"process start failed: {type(error).__name__}: {error}",
                }
            )
            status = "failed"
            break

    post_attestation: dict[str, object] | None = None
    try:
        post_attestation = attest_workspace(workspace, resolved_sha)
    except WorkloadIsolationError as error:
        status = "failed"
        steps.append({"command": ["workload-attestation"], "status": "failed", "reason": str(error)})

    return catalog.write_result(
        plan,
        result_path,
        status,
        steps=steps,
        command_contract_sha256=command_contract_sha256(sequence),
        validated_gates=["node-syntax", "evidence-policy-tests"],
        runtime={"node_major": node_major},
        workspace_attestation={"before": pre_attestation, "after": post_attestation},
    )
