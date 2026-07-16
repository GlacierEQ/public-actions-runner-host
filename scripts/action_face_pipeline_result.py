#!/usr/bin/env python3
"""Create a governed blocked result when a claimed pipeline cannot execute a workload."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

SAFE_OUTCOME = re.compile(r"^(success|failure|cancelled|skipped|)$")


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def outcome(name: str) -> str:
    value = os.environ.get(name, "")
    return value if SAFE_OUTCOME.fullmatch(value) else "invalid"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default=".apex-plan.json")
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    result_path = Path(args.result).resolve()
    if result_path.exists():
        output("synthesized", "false")
        print("PIPELINE_RESULT_OK: adapter result already exists; synthesis not needed")
        return 0
    if not plan_path.is_file():
        raise SystemExit("plan file does not exist")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise SystemExit("plan must be a JSON object")

    stages = {
        "authorization": outcome("AUTHORIZATION_OUTCOME"),
        "action_face_identity": outcome("IDENTITY_OUTCOME"),
        "credential_gate": outcome("CREDENTIAL_OUTCOME"),
        "control_plane_guard": outcome("CONTROL_PLANE_OUTCOME"),
        "plan": outcome("PLAN_OUTCOME"),
        "claim": outcome("CLAIM_OUTCOME"),
        "approval": outcome("APPROVAL_OUTCOME"),
        "checkout": outcome("CHECKOUT_OUTCOME"),
        "runner": outcome("RUNNER_OUTCOME"),
    }
    failed_stages = [name for name, state in stages.items() if state in {"failure", "cancelled", "invalid"}]
    skipped_stages = [name for name, state in stages.items() if state == "skipped"]
    reason = "adapter exited without producing a result file"
    if failed_stages:
        reason = f"pipeline blocked at: {', '.join(failed_stages)}"
    elif skipped_stages:
        reason = f"pipeline did not execute stages: {', '.join(skipped_stages)}"

    provenance_keys = (
        "workflow_run_id",
        "workflow_run_attempt",
        "trigger_actor",
        "trigger_actor_id",
        "event_name",
        "execution_repo",
        "public_runner_sha",
    )
    result = {
        "schema_version": "1.1",
        "job_id": plan.get("job_id"),
        "pillar": plan.get("pillar"),
        "action": plan.get("action", ""),
        "adapter": plan.get("adapter", ""),
        "task": plan.get("task", ""),
        "source_repo": plan.get("source_repo"),
        "source_ref": plan.get("source_ref"),
        "target_repo": plan.get("target_repo", ""),
        "provenance": {key: plan.get(key, "") for key in provenance_keys if plan.get(key, "")},
        "status": "blocked",
        "reason": reason,
        "stage_outcomes": stages,
        "synthesized": True,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output("synthesized", "true")
    print(f"PIPELINE_RESULT_OK: synthesized blocked result for {result.get('job_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
