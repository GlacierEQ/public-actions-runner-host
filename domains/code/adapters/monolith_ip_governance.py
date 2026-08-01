#!/usr/bin/env python3
"""Code-domain wrapper for the existing Monolith IP-governance adapter.

This is deliberately a migration wrapper, not a second implementation. It
rejects cross-domain or caller-selected execution targets, then delegates the
unchanged plan and paths to the battle-tested legacy adapter. The compatibility
alias remains available until one verified private domain run proves parity.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import apex_catalog_runner as catalog
import monolith_ip_governance_adapter as legacy

DOMAIN = "code"
CANONICAL_ACTION = "code.validate-governance"
LEGACY_ACTION = "monolith-ip-governance"
ADAPTER = "monolith_ip_governance"
LEGACY_ADAPTER = "monolith-ip-governance"
TARGET_REPOSITORY = "GlacierEQ/monolith"
TOKEN_PROFILE = "private-source-read"
ALLOWED_ACTIONS = frozenset({CANONICAL_ACTION, LEGACY_ACTION})
ALLOWED_ADAPTERS = frozenset({ADAPTER, LEGACY_ADAPTER})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def adapter_control_hashes() -> dict[str, str]:
    """Bind both the migration wrapper and preserved execution core."""
    return {
        "domain_wrapper": sha256_file(Path(__file__)),
        "legacy_execution_core": sha256_file(Path(legacy.__file__)),
    }


def validate_plan(plan: object) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("domain adapter plan must be a JSON object")

    domain = plan.get("domain")
    if domain not in (None, "", DOMAIN):
        raise ValueError("cross-domain execution is forbidden")

    action = plan.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("action is not registered to the Monolith code adapter")

    adapter = plan.get("adapter")
    if adapter not in (None, "", *ALLOWED_ADAPTERS):
        raise ValueError("caller-selected adapter is forbidden")

    for key in ("source_repo", "target_repo"):
        value = plan.get(key)
        if value not in (None, "", TARGET_REPOSITORY):
            raise ValueError(f"{key} is not the catalog-bound repository")

    task = plan.get("task")
    if task not in (None, "", "test"):
        raise ValueError("Monolith governance task must remain test")
    return plan


def blocked_plan(plan: object) -> dict[str, Any]:
    """Build the minimum safe result identity without trusting invalid fields."""
    source = plan if isinstance(plan, dict) else {}
    job_id = source.get("job_id")
    pillar = source.get("pillar")
    return {
        "job_id": (
            job_id
            if isinstance(job_id, str) and 8 <= len(job_id) <= 64
            else "invalid-domain-plan"
        ),
        "pillar": pillar if pillar == "D" else "D",
        "action": CANONICAL_ACTION,
        "adapter": ADAPTER,
        "task": "test",
        "source_repo": TARGET_REPOSITORY,
        "source_ref": (
            source.get("source_ref")
            if isinstance(source.get("source_ref"), str)
            else ""
        ),
        "target_repo": TARGET_REPOSITORY,
    }


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    """Validate domain boundaries and delegate with byte-for-byte plan parity."""
    try:
        validated = validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(
            blocked_plan(plan),
            result_path,
            "blocked",
            reason=str(error),
            domain=DOMAIN,
            canonical_action=CANONICAL_ACTION,
        )
    return legacy.run(validated, workspace, result_path)


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: monolith_ip_governance.py PLAN WORKSPACE RESULT"
        )
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return run(plan, workspace, result_path)


if __name__ == "__main__":
    raise SystemExit(main())
