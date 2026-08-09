#!/usr/bin/env bash
set -euo pipefail

python -m pip install \
  --disable-pip-version-check \
  --only-binary=:all: \
  ruff==0.16.1 \
  pytest==9.1.1

python -m json.tool config/action-face-actions.json >/dev/null
python -m json.tool registry/domains.json >/dev/null
python -m json.tool registry/actions-index.json >/dev/null
python -m json.tool registry/receipt-namespaces.json >/dev/null
python -m json.tool domains/code/actions.json >/dev/null
python -m json.tool domains/docs/actions.json >/dev/null
python -m json.tool domains/analysis/actions.json >/dev/null
python -m json.tool domains/code/schemas/monolith-atlases-job.schema.json >/dev/null
python -m json.tool domains/code/schemas/monolith-atlases-result.schema.json >/dev/null
python -m json.tool domains/docs/schemas/job.schema.json >/dev/null
python -m json.tool domains/analysis/schemas/job.schema.json >/dev/null

OWNED_FILES=(
  domains/code/adapters/tool_system_validate.py
  domains/code/adapters/monolith_atlas_validate.py
  domains/code/adapters/monolith_legal_live_validate.py
  domains/code/adapters/mega_pdf_function_genome.py
  domains/docs/adapters/monolith_docs_validate.py
  domains/analysis/adapters/monolith_estate_health.py
  scripts/action_face_checkout_workload.py
  scripts/action_face_postrun_guard.py
  scripts/action_face_publish_verified.py
  scripts/action_face_selftest.py
  scripts/monolith_evolution_adapter.py
  scripts/workload_isolation.py
  tests/conftest.py
  tests/test_action_face_checkout_workload.py
  tests/test_apex_job_ingress_exclusivity.py
  tests/test_bounded_file_receipts.py
  tests/test_isolated_catalog_runner_import.py
  tests/test_mega_pdf_function_genome_recovery.py
  tests/test_monolith_atlas_optional_category.py
  tests/test_monolith_legal_live_safe_path.py
  tests/test_monolith_evolution_adapter.py
  tests/test_publish_verified_boundary.py
  tests/test_tool_system_validate_adapter.py
  tests/test_specialized_monolith_runners.py
  tests/test_workload_isolation.py
)

FORMAT_FILES=(
  domains/code/adapters/tool_system_validate.py
  domains/code/adapters/monolith_atlas_validate.py
  domains/code/adapters/monolith_legal_live_validate.py
  domains/code/adapters/mega_pdf_function_genome.py
  domains/docs/adapters/monolith_docs_validate.py
  domains/analysis/adapters/monolith_estate_health.py
  scripts/action_face_checkout_workload.py
  scripts/action_face_postrun_guard.py
  scripts/action_face_publish_verified.py
  scripts/monolith_evolution_adapter.py
  scripts/workload_isolation.py
  tests/conftest.py
  tests/test_action_face_checkout_workload.py
  tests/test_apex_job_ingress_exclusivity.py
  tests/test_bounded_file_receipts.py
  tests/test_isolated_catalog_runner_import.py
  tests/test_mega_pdf_function_genome_recovery.py
  tests/test_monolith_atlas_optional_category.py
  tests/test_monolith_legal_live_safe_path.py
  tests/test_monolith_evolution_adapter.py
  tests/test_publish_verified_boundary.py
  tests/test_tool_system_validate_adapter.py
  tests/test_specialized_monolith_runners.py
  tests/test_workload_isolation.py
)

# The GitHub contents API cannot set executable mode bits, so EXE001 is the
# only suppressed rule for the fully owned hardening files. The established
# executable canary remains linted and compiled but retains its historical
# hand-formatted layout.
ruff check --ignore EXE001 "${OWNED_FILES[@]}"
ruff format --check "${FORMAT_FILES[@]}"

# These established files contain pre-existing style debt. Enforce correctness
# rules while explicitly excluding only the known legacy executable/import rules.
ruff check --ignore EXE001,I001,PIE810 \
  scripts/action_face_catalog_runner.py \
  scripts/action_face_plan.py \
  scripts/apex_catalog_runner.py

python -m compileall -q dispatcher domains scripts tests
pytest -x -q
python scripts/verify_github_app_bridge_contract.py

# The reusable CI contract requires a bounded proof artifact. Emit it only after
# every repository-owned verification command above succeeds so artifact upload
# and verification truth cannot diverge.
mkdir -p .verification-artifacts
python - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

receipt = {
    "schema": "glaciereq.public-action-face-verification-receipt.v1",
    "status": "pass",
    "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
    "commit": os.environ.get("GITHUB_SHA", "local"),
    "workflow_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
    "checks": [
        "catalog-json",
        "ruff",
        "format",
        "compileall",
        "pytest",
        "github-app-bridge-contract",
    ],
}
Path(".verification-artifacts/public-action-face-verification.json").write_text(
    json.dumps(receipt, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
