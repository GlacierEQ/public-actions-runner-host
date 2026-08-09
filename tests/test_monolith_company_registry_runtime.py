from __future__ import annotations

import sys
from pathlib import Path

from domains.code.adapters import monolith_company_registry_validate as company_registry


def test_company_registry_commands_provision_pinned_pytest(tmp_path: Path) -> None:
    result = tmp_path / "results" / "job.json"
    sequence = company_registry.commands(result, "CompanyRegistryJob01")
    venv = result.resolve().parent / "venv-CompanyRegistryJob01"
    python = venv / "bin" / "python"

    assert sequence[0] == [sys.executable, "-m", "venv", str(venv)]
    assert sequence[1] == [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        f"pytest=={company_registry.PYTEST_VERSION}",
    ]
    assert sequence[2][0] == str(python)
    assert sequence[3] == [
        str(python),
        "-m",
        "pytest",
        "-q",
        "tests/test_company_engineered_registry.py",
    ]
