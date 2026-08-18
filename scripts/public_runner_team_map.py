#!/usr/bin/env python3
"""Map public runner topology without converting drift into automatic paralysis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "public-runner-team.json"
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PRIVATE_MARKERS = ("self-hosted", "private-actions-runner")


def inspect_workflows(config: dict[str, Any]) -> dict[str, Any]:
    policy = config["runner_policy"]
    prefixes = tuple(policy["allowed_runner_prefixes"])
    observations: list[dict[str, Any]] = []
    checked = 0

    for workflow in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        checked += 1
        text = workflow.read_text(encoding="utf-8")
        private_markers = [marker for marker in PRIVATE_MARKERS if marker in text]
        runner_values = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip().startswith("runs-on:"):
                continue
            runner = line.split(":", 1)[1].split("#", 1)[0].strip().strip("'\"")
            runner_values.append(
                {
                    "line": line_number,
                    "runner": runner,
                    "recognized_public_host": runner.startswith(prefixes),
                }
            )
        observations.append(
            {
                "workflow": workflow.name,
                "private_markers": private_markers,
                "runs_on": runner_values,
            }
        )

    nonstandard = [
        {
            "workflow": item["workflow"],
            "runner": run["runner"],
            "line": run["line"],
        }
        for item in observations
        for run in item["runs_on"]
        if not run["recognized_public_host"]
    ]
    private_references = [
        {"workflow": item["workflow"], "markers": item["private_markers"]}
        for item in observations
        if item["private_markers"]
    ]
    status = "topology_mapped" if not nonstandard and not private_references else "topology_expansion_available"
    return {
        "schema_version": "1.0",
        "event": "public_runner_topology",
        "status": status,
        "execution_mode": config.get("execution_mode"),
        "preferred_runner": policy.get("preferred_runner"),
        "checked_workflows": checked,
        "nonstandard_runners": nonstandard,
        "private_references": private_references,
        "workflows": observations,
        "continuation": "enabled",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-mapped-topology",
        action="store_true",
        help="Return a non-zero exit only when a caller explicitly requires an unexpanded topology report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    report = inspect_workflows(config)
    print("PUBLIC_RUNNER_TOPOLOGY: " + json.dumps(report, sort_keys=True))
    if args.require_mapped_topology and report["status"] != "topology_mapped":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
