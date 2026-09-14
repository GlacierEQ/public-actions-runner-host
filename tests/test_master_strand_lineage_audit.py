from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "master_strand_extinction.py"


def _module():
    spec = importlib.util.spec_from_file_location("master_strand_lineage_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class CompareAPI:
    def __init__(self, ahead_by: int):
        self.ahead_by = ahead_by

    def compare(self, full_name: str, base: str, head: str):
        return {
            "ahead_by": self.ahead_by,
            "behind_by": 2,
            "status": "behind" if self.ahead_by == 0 else "diverged",
            "base_commit": {"sha": "a" * 40},
            "head_commit": {"sha": "b" * 40},
        }


def _repo():
    return {"full_name": "GlacierEQ/example", "default_branch": "main"}


def _branch(name: str):
    return {"name": name, "commit": {"sha": "b" * 40}}


def test_commit_containment_never_authorizes_ref_deletion():
    module = _module()
    decision = module.branch_decision(CompareAPI(0), _repo(), _branch("feature/donor"))

    assert decision.disposition == "PRESERVE_DRAINED_LINEAGE_CANDIDATE"
    assert decision.delete_ready is False
    assert decision.deleted is False
    assert decision.unique_contribution_state == "CANDIDATE_UNVERIFIED"
    assert decision.lineage_state == "ACTIVE_IN_MESH"
    assert "UNIQUE_CONTRIBUTION=0" in decision.blocker


def test_nonzero_commit_delta_remains_active_in_mesh():
    module = _module()
    decision = module.branch_decision(CompareAPI(3), _repo(), _branch("feature/donor"))

    assert decision.disposition == "ABSORB_OR_TRANSPLANT"
    assert decision.delete_ready is False
    assert decision.unique_contribution_state == "NONZERO_COMMIT_DELTA_CONFIRMED"
    assert decision.lineage_state == "ACTIVE_IN_MESH"


@pytest.mark.parametrize("verb", ["POST", "PUT", "PATCH", "DELETE"])
def test_provider_adapter_rejects_mutation_methods(verb: str):
    module = _module()
    api = module.GitHubAPI("read-token")

    with pytest.raises(module.ExtinctionError, match="read-only"):
        api.request("/repos/GlacierEQ/example/git/refs/heads/donor", method=verb)


def test_source_exposes_no_delete_branch_method():
    module = _module()
    assert not hasattr(module.GitHubAPI, "delete_branch")
