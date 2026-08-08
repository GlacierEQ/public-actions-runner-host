"""Run the fixed Monolith atlas gates that cannot allocate private runners."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import apex_catalog_runner as catalog

from scripts.workload_isolation import (
    WorkloadIsolationError,
    attest_checkout,
    build_environment,
    command_contract_sha256,
    open_checkout,
)

EXPECTED_ACTION = "code.monolith.validate-atlases"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "test"
SHA = re.compile(r"^[0-9a-f]{40}$")
CORE_REQUIRED_PATHS = (
    "scripts/validate_function_atlas.py",
    "tests/test_function_atlas.py",
    "scripts/build_monolith_command_atlas.py",
    "scripts/query_monolith.py",
    "tests/test_monolith_command_atlas.py",
    "tests/test_query_monolith.py",
    "catalog/library.json",
    "catalog/monolith_command_atlas.json",
    "status/MONOLITH_COMMAND_ATLAS.md",
)
CONNECTOR_REQUIRED_PATHS = (
    "scripts/validate_connector_fabric.py",
    "tests/test_connector_fabric.py",
    "domains/mcp_connectors.md",
    "status/CONNECTOR_FABRIC_ATLAS.md",
)
CATEGORY_REQUIRED_PATHS = (
    "scripts/validate_category_heads.py",
    "tests/test_category_heads.py",
    "catalog/category_heads.json",
    "foundations/category-heads.md",
)
COMMAND_ATLAS_GENERATOR = "scripts/build_monolith_command_atlas.py"
COMMAND_ATLAS_REPAIR_INPUTS = (
    COMMAND_ATLAS_GENERATOR,
    "catalog/library.json",
    "evidence/system_maps/control_plane_orchestration.json",
)
COMMAND_ATLAS_FACT_CARDS = "evidence/repository_fact_cards/control_plane_orchestration"
COMMAND_ATLAS_OUTPUTS = (
    "catalog/monolith_command_atlas.json",
    "status/MONOLITH_COMMAND_ATLAS.md",
)
MAX_REPAIR_FILE_BYTES = 250_000
MAX_REPAIR_TOTAL_BYTES = 500_000


class ProjectionRepairError(RuntimeError):
    """Raised when a deterministic projection repair cannot be produced safely."""


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


def connector_surface_state(workspace: Path) -> str:
    present = [path for path in CONNECTOR_REQUIRED_PATHS if (workspace / path).is_file()]
    if not present:
        return "absent"
    if len(present) == len(CONNECTOR_REQUIRED_PATHS):
        return "complete"
    return "partial"


def category_surface_state(workspace: Path) -> str:
    present = [path for path in CATEGORY_REQUIRED_PATHS if (workspace / path).is_file()]
    if not present:
        return "absent"
    if len(present) == len(CATEGORY_REQUIRED_PATHS):
        return "complete"
    return "partial"


def commands(
    result_path: Path,
    job_id: str,
    include_category_heads: bool = True,
    include_connectors: bool = True,
) -> list[list[str]]:
    venv = result_path.resolve().parent / f"venv-{job_id}"
    python = venv / "bin" / "python"
    compile_targets = [
        "scripts/validate_function_atlas.py",
        "tests/test_function_atlas.py",
    ]
    if include_connectors:
        compile_targets.extend(
            [
                "scripts/validate_connector_fabric.py",
                "tests/test_connector_fabric.py",
            ]
        )
    if include_category_heads:
        compile_targets.extend(
            [
                "scripts/validate_category_heads.py",
                "tests/test_category_heads.py",
            ]
        )

    sequence: list[list[str]] = [
        [sys.executable, "-m", "venv", str(venv)],
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "pytest==8.4.1",
        ],
        [str(python), "-m", "py_compile", *compile_targets],
        [str(python), "scripts/validate_function_atlas.py"],
        [
            str(python),
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_function_atlas.py",
        ],
    ]

    if include_connectors:
        sequence.extend(
            [
                [str(python), "scripts/validate_connector_fabric.py"],
                [
                    str(python),
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_connector_fabric.py",
                ],
            ]
        )

    if include_category_heads:
        sequence.extend(
            [
                [str(python), "scripts/validate_category_heads.py"],
                [
                    str(python),
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_category_heads.py",
                ],
            ]
        )

    sequence.extend(
        [
            [str(python), COMMAND_ATLAS_GENERATOR, "--check"],
            [str(python), "scripts/query_monolith.py", "summary", "--format", "json"],
            [
                str(python),
                "scripts/query_monolith.py",
                "repos",
                "--has-evidence",
                "--format",
                "json",
                "--limit",
                "0",
            ],
            [
                str(python),
                "scripts/query_monolith.py",
                "actions",
                "--priority",
                "P0",
                "--format",
                "json",
            ],
            [str(python), "scripts/query_monolith.py", "domains", "--format", "json"],
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                "tests/test_monolith_command_atlas.py",
                "tests/test_query_monolith.py",
            ],
        ]
    )
    return sequence


def _copy_regular_file(workspace: Path, scratch: Path, relative: str) -> None:
    source = workspace / relative
    if source.is_symlink() or not source.is_file():
        raise ProjectionRepairError(f"repair input is not a regular file: {relative}")
    destination = scratch / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def command_atlas_projection_repair(
    workspace: Path,
    result_path: Path,
    job_id: str,
    env: dict[str, str],
    resolved_sha: str,
) -> dict:
    """Generate exact replacement projections outside the attested checkout."""

    scratch = result_path.resolve().parent / f"projection-repair-{job_id}"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)

    for relative in COMMAND_ATLAS_REPAIR_INPUTS:
        _copy_regular_file(workspace, scratch, relative)

    fact_card_source = workspace / COMMAND_ATLAS_FACT_CARDS
    if fact_card_source.is_symlink() or not fact_card_source.is_dir():
        raise ProjectionRepairError("command-atlas fact-card directory is unavailable")
    fact_cards = sorted(fact_card_source.glob("*.json"))
    if not fact_cards:
        raise ProjectionRepairError("command-atlas fact-card population is empty")
    for source in fact_cards:
        if source.is_symlink() or not source.is_file():
            raise ProjectionRepairError(
                f"fact-card repair input is not a regular file: {source.name}"
            )
        relative = f"{COMMAND_ATLAS_FACT_CARDS}/{source.name}"
        _copy_regular_file(workspace, scratch, relative)

    process = subprocess.run(
        [sys.executable, COMMAND_ATLAS_GENERATOR],
        cwd=scratch,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
        shell=False,
    )
    if process.returncode != 0:
        output = (process.stdout or "")[-4_000:]
        raise ProjectionRepairError(
            f"command-atlas regeneration failed with exit {process.returncode}: {output}"
        )

    files: list[dict[str, object]] = []
    total_bytes = 0
    for relative in COMMAND_ATLAS_OUTPUTS:
        path = scratch / relative
        if path.is_symlink() or not path.is_file():
            raise ProjectionRepairError(
                f"generator did not create regular output: {relative}"
            )
        payload = path.read_bytes()
        if len(payload) > MAX_REPAIR_FILE_BYTES:
            raise ProjectionRepairError(
                f"generated repair file exceeds bound: {relative}"
            )
        total_bytes += len(payload)
        if total_bytes > MAX_REPAIR_TOTAL_BYTES:
            raise ProjectionRepairError(
                "generated repair payload exceeds total byte bound"
            )
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProjectionRepairError(
                f"generated repair file is not UTF-8: {relative}"
            ) from error
        files.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "content": content,
            }
        )

    return {
        "status": "available",
        "generator": COMMAND_ATLAS_GENERATOR,
        "resolved_source_sha": resolved_sha,
        "fact_card_count": len(fact_cards),
        "total_bytes": total_bytes,
        "files": files,
    }


def _is_command_atlas_check(command: list[str]) -> bool:
    return command[-2:] == [COMMAND_ATLAS_GENERATOR, "--check"]


def run(plan: dict, workspace: Path, result_path: Path) -> int:
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

    try:
        checkout = open_checkout(workspace, label="workload")
        env = build_environment(result_path, str(plan["job_id"]))
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"workload isolation failed before execution: {error}",
        )

    projection_repair: dict | None = None
    with checkout:
        try:
            pre_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            return catalog.write_result(
                plan,
                result_path,
                "blocked",
                reason=f"workload isolation failed before execution: {error}",
            )

        workspace_root = checkout.proc_path
        missing_core = [
            path
            for path in CORE_REQUIRED_PATHS
            if not (workspace_root / path).is_file()
        ]
        if missing_core:
            return catalog.write_result(
                plan,
                result_path,
                "blocked",
                reason=(
                    "required Monolith atlas files are missing: "
                    + ", ".join(missing_core)
                ),
            )

        connector_state = connector_surface_state(workspace_root)
        if connector_state == "partial":
            missing_connectors = [
                path
                for path in CONNECTOR_REQUIRED_PATHS
                if not (workspace_root / path).is_file()
            ]
            return catalog.write_result(
                plan,
                result_path,
                "blocked",
                reason=(
                    "partial connector-fabric surface is not verifiable; missing: "
                    + ", ".join(missing_connectors)
                ),
            )

        category_state = category_surface_state(workspace_root)
        if category_state == "partial":
            missing_category = [
                path
                for path in CATEGORY_REQUIRED_PATHS
                if not (workspace_root / path).is_file()
            ]
            return catalog.write_result(
                plan,
                result_path,
                "blocked",
                reason=(
                    "partial category-head surface is not verifiable; missing: "
                    + ", ".join(missing_category)
                ),
            )

        include_connectors = connector_state == "complete"
        include_category_heads = category_state == "complete"
        sequence = commands(
            result_path,
            str(plan["job_id"]),
            include_category_heads,
            include_connectors,
        )
        steps: list[dict] = []
        status = "completed"
        for command in sequence:
            try:
                process = subprocess.run(
                    command,
                    cwd=workspace_root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=1800,
                    check=False,
                    shell=False,
                    env=env,
                    pass_fds=checkout.pass_fds,
                )
                output = (process.stdout or "")[-100_000:]
                steps.append(
                    {
                        "command": command,
                        "exit_code": process.returncode,
                        "status": (
                            "completed" if process.returncode == 0 else "failed"
                        ),
                        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                        "output_tail": output[-24_000:],
                    }
                )
                if process.returncode != 0:
                    status = "failed"
                    if _is_command_atlas_check(command):
                        try:
                            projection_repair = command_atlas_projection_repair(
                                workspace_root,
                                result_path,
                                str(plan["job_id"]),
                                env,
                                resolved_sha,
                            )
                        except ProjectionRepairError as error:
                            steps.append(
                                {
                                    "command": ["projection-repair"],
                                    "status": "failed",
                                    "reason": str(error),
                                }
                            )
                    break
            except subprocess.TimeoutExpired as error:
                output = error.stdout if isinstance(error.stdout, str) else ""
                steps.append(
                    {
                        "command": command,
                        "status": "failed",
                        "reason": "timeout after 1800 seconds",
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
                        "reason": (
                            f"process start failed: {type(error).__name__}: {error}"
                        ),
                    }
                )
                status = "failed"
                break

        post_attestation: dict[str, object] | None = None
        try:
            post_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            status = "failed"
            steps.append(
                {
                    "command": ["workload-attestation"],
                    "status": "failed",
                    "reason": str(error),
                }
            )

    gates = ["core-function-atlas"]
    if include_connectors:
        gates.append("connector-fabric-atlas")
    if include_category_heads:
        gates.append("category-head-hierarchy")
    gates.append("monolith-command-atlas")

    details: dict[str, object] = {
        "steps": steps,
        "command_contract_sha256": command_contract_sha256(
            sequence,
            volatile_roots=(result_path.parent,),
        ),
        "validated_gates": gates,
        "workspace_attestation": {
            "before": pre_attestation,
            "after": post_attestation,
        },
    }
    if projection_repair is not None:
        details["projection_repair"] = projection_repair

    return catalog.write_result(
        plan,
        result_path,
        status,
        **details,
    )
