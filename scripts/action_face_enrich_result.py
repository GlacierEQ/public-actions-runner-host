#!/usr/bin/env python3
"""Enrich a produced result with immutable routing metadata without masking conflicts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROUTING_FIELDS = (
    "job_id",
    "pillar",
    "action",
    "adapter",
    "task",
    "source_repo",
    "source_ref",
    "target_repo",
)


def fail(message: str) -> None:
    raise SystemExit(f"RESULT_ENRICH_BLOCK: {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    result_path = Path(args.result).resolve()
    if not plan_path.is_file():
        fail("plan file does not exist")
    if not result_path.exists():
        print("RESULT_ENRICH_OK: no adapter result exists; synthesis will govern the lifecycle")
        return 0
    if result_path.is_symlink() or not result_path.is_file():
        fail("result is not a regular file")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(result, dict):
        fail("plan and result must be JSON objects")
    if "receipt" in result:
        fail("untrusted adapter result already contains a receipt")

    changed = []
    for field in ROUTING_FIELDS:
        expected = plan.get(field, "")
        actual = result.get(field, "")
        if actual not in (None, "") and str(actual) != str(expected):
            fail(f"adapter result conflicts with immutable plan field {field}")
        if field not in result or result.get(field) in (None, ""):
            result[field] = expected
            changed.append(field)

    expected_provenance = {
        key: plan.get(key, "")
        for key in (
            "workflow_run_id",
            "workflow_run_attempt",
            "trigger_actor",
            "trigger_actor_id",
            "event_name",
            "execution_repo",
            "public_runner_sha",
        )
        if plan.get(key, "")
    }
    actual_provenance = result.get("provenance")
    if actual_provenance not in (None, {}) and actual_provenance != expected_provenance:
        fail("adapter result provenance conflicts with immutable plan")
    if actual_provenance in (None, {}):
        result["provenance"] = expected_provenance
        changed.append("provenance")

    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"RESULT_ENRICH_OK: normalized {', '.join(changed) if changed else 'no'} fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
