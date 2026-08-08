"""Regression for delegated catalog execution under the capability-minimized env."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from workload_isolation import build_environment

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_runner_imports_siblings_with_python_safe_path(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    environment = build_environment(result_path, "catalog-import-proof")

    process = subprocess.run(
        [sys.executable, "scripts/apex_catalog_runner.py"],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
        shell=False,
    )

    assert environment["PYTHONSAFEPATH"] == "1"
    assert process.returncode != 0
    assert "usage: apex_catalog_runner.py PLAN WORKSPACE RESULT" in process.stdout
    assert "ModuleNotFoundError" not in process.stdout
