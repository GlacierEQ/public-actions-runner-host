from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dispatcher import domain_registry as registry


def test_registry_validates_and_only_code_is_executable() -> None:
    actions = registry.validate_registry()
    assert set(actions) == {"code.validate-governance"}
    action = actions["code.validate-governance"]
    assert action["domain"] == "code"
    assert action["targetRepository"] == "GlacierEQ/monolith"
    assert action["adapter"] == "monolith_ip_governance"
    assert action["receiptRoot"] == "receipts/code"


def test_legacy_alias_resolves_to_the_same_canonical_action() -> None:
    canonical = registry.resolve_action(
        "code.validate-governance", requested_domain="code"
    )
    legacy = registry.resolve_action(
        "monolith-ip-governance", requested_domain="code"
    )
    assert canonical["canonicalAction"] == "code.validate-governance"
    assert canonical["wasAlias"] is False
    assert legacy["canonicalAction"] == canonical["canonicalAction"]
    assert legacy["targetRepository"] == canonical["targetRepository"]
    assert legacy["tokenProfile"] == canonical["tokenProfile"]
    assert legacy["wasAlias"] is True


def test_unknown_and_planned_actions_cannot_resolve() -> None:
    with pytest.raises(registry.RegistryError, match="not registered"):
        registry.resolve_action("code.not-real")
    with pytest.raises(registry.RegistryError, match="not registered"):
        registry.resolve_action("docs.generate-pdf", requested_domain="docs")


def test_cross_domain_resolution_is_rejected() -> None:
    with pytest.raises(registry.RegistryError, match="does not own"):
        registry.resolve_action(
            "code.validate-governance", requested_domain="analysis"
        )


def test_code_token_profile_is_exact_and_read_only() -> None:
    action = registry.resolve_action("code.validate-governance")
    profile = action["tokenProfileContract"]
    assert profile["repositoryCount"] == 1
    assert profile["repositorySelection"] == "catalog-exact"
    assert profile["permissions"] == {"contents": "read"}
    assert profile["persistCredentials"] is False
    assert profile["exposeCredentialToWorkload"] is False
    assert profile["sourceWrites"] == "forbidden"
    assert profile["resultWrites"] == "control-plane-receipt-only"


def test_repository_paths_cannot_escape_the_checkout(tmp_path: Path) -> None:
    with pytest.raises(registry.RegistryError, match="unsafe registry path"):
        registry.safe_repository_path(
            tmp_path, "../outside.json", must_exist=False
        )
    with pytest.raises(registry.RegistryError, match="POSIX relative"):
        registry.safe_repository_path(
            tmp_path, "domains\\code\\actions.json", must_exist=False
        )
