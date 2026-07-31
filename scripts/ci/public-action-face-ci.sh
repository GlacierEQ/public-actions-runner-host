#!/usr/bin/env bash
set -euo pipefail

python -m pip install \
  --disable-pip-version-check \
  --only-binary=:all: \
  ruff==0.16.1 \
  pytest==9.1.1

python -m json.tool config/action-face-actions.json >/dev/null

ruff check \
  scripts/monolith_evolution_adapter.py \
  tests/test_monolith_evolution_adapter.py
ruff format --check \
  scripts/monolith_evolution_adapter.py \
  tests/test_monolith_evolution_adapter.py

# These established files contain pre-existing style debt. Enforce correctness
# rules while explicitly excluding only the known legacy executable/import rules.
ruff check --ignore EXE001,I001,PIE810 \
  scripts/action_face_catalog_runner.py \
  scripts/action_face_plan.py

python -m compileall -q scripts tests
pytest -x -q
python scripts/verify_github_app_bridge_contract.py
