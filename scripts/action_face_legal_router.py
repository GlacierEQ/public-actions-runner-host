#!/usr/bin/env python3
"""Narrow legal specialization router for the canonical APEX public action face."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: action_face_legal_router.py PLAN WORKSPACE RESULT")
    plan_path, workspace, result_path = map(Path, sys.argv[1:])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("action") == "docket-sync":
        from domains.legal.adapters.jefs_docket_acquire import run

        return run(plan, workspace, result_path)
    return subprocess.call(
        [
            sys.executable,
            "scripts/action_face_catalog_runner.py",
            str(plan_path),
            str(workspace),
            str(result_path),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
