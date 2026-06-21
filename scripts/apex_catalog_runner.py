#!/usr/bin/env python3
"""Execute a cataloged pillar action through a safe public adapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import apex_pillar_runner as base

SUPPORTED = {
    "hash-manifest": "hash-manifest",
    "validate": "validate",
    "test": "test",
    "audit": "audit",
    "document-validate": "validate",
}


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: apex_catalog_runner.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text())
    adapter = plan.get("adapter")
    if not adapter:
        return base.execute(plan, workspace, result_path)
    task = SUPPORTED.get(adapter)
    if not task:
        result = {
            "schema_version": "1.0",
            "job_id": plan["job_id"],
            "pillar": plan["pillar"],
            "action": plan.get("action"),
            "adapter": adapter,
            "status": "blocked",
            "reason": "Dedicated public adapter requires runtime or secret migration",
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"Action {plan.get('action')} is routed but its dedicated adapter is not active.")
        return 2
    executable = dict(plan)
    executable["task"] = task
    return base.execute(executable, workspace, result_path)


if __name__ == "__main__":
    raise SystemExit(main())
