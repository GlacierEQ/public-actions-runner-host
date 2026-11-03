"""Test that absent private-checkout verification cannot be called a passing run."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/private-estate-non-regression-reusable.yml"
ENFORCE = "      - name: Enforce result after status publication\n"


def enforcement_script() -> str:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert source.count(ENFORCE) == 1, "expected exactly one private CI result gate"
    tail = source.split(ENFORCE, 1)[1]
    assert "if: always()" in tail.split("        run: |", 1)[0]
    assert "CONFIGURED:" in tail and "EXIT_CODE:" in tail
    return textwrap.dedent(tail.split("        run: |\n", 1)[1])


class PrivateEstateWatchdogTruthTests(unittest.TestCase):
    def run_enforcement(self, configured: str, exit_code: str):
        env = dict(os.environ, CONFIGURED=configured, EXIT_CODE=exit_code)
        return subprocess.run(
            ["bash", "-e", "-c", enforcement_script()],
            text=True, capture_output=True, env=env, check=False,
        )

    def test_missing_credentials_are_failed_not_green(self):
        process = self.run_enforcement("false", "")
        self.assertEqual(process.returncode, 2)
        self.assertIn("Private estate not verified", process.stderr)

    def test_missing_verification_receipt_is_failed_not_green(self):
        self.assertNotEqual(self.run_enforcement("true", "").returncode, 0)

    def test_private_verification_failure_is_not_green(self):
        self.assertNotEqual(self.run_enforcement("true", "17").returncode, 0)

    def test_actual_verification_exit_zero_is_success(self):
        self.assertEqual(self.run_enforcement("true", "0").returncode, 0)


if __name__ == "__main__":
    unittest.main()
