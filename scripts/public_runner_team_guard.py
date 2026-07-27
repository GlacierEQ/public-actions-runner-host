#!/usr/bin/env python3
"""Fail closed if the public action face drifts toward private runners."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "public-runner-team.json"
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PRIVATE_MARKERS = ("self-hosted", "llm-runner-teams", "private-actions-runner")


def fail(message: str) -> None:
    print(f"::error::{message}")


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    policy = config["runner_policy"]
    prefixes = tuple(policy["allowed_runner_prefixes"])
    failures: list[str] = []
    checked = 0

    for workflow in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        checked += 1
        text = workflow.read_text(encoding="utf-8")
        for marker in PRIVATE_MARKERS:
            if marker in text:
                failures.append(f"{workflow.name}: forbidden runner/control-plane marker {marker!r}")
        for line in text.splitlines():
            if not line.strip().startswith("runs-on:"):
                continue
            runner = line.split(":", 1)[1].split("#", 1)[0].strip()
            if runner[:1] in ("'", '"'):
                runner = runner[1:-1]
            if not runner.startswith(prefixes):
                failures.append(f"{workflow.name}: runs-on {runner!r} is outside the public hosted runner policy")

    print(f"Public runner team guard: checked {checked} workflow files")
    print(f"Canonical runner: {policy['canonical_runner']}")
    print(f"Execution mode: {config['execution_mode']}")
    if failures:
        for failure in failures:
            fail(failure)
        return 1
    print("Public runner team guard: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
