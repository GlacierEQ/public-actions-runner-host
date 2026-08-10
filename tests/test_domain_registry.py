from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dispatcher import domain_registry as registry


def copy_registry_fixture(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "registry", tmp_path / "registry")
    (tmp_path / "domains").mkdir()
    for domain in ("code", "docs", "analysis"):
        shutil.copytree(ROOT / "domains" / domain, tmp_path / "domains" / domain)
    return tmp_path


def mutate_json(path: Path, mutation: Callable[[dict], None]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_registry_validates_all_specialized_domains() -> None:
    actions = registry.validate_registry()
    assert set(actions) == {
        "code.tool-system.validate",
        "code.validate-governance",
        "code.monolith.validate-atlases",
        "code.monolith.validate-legal-live-reconciliation",
        "code.monolith.validate-company-engineered-registry",
        "code.casey-legal-mcp.validate-v2",
        "code.fileboss.validate-operator-code-bridge",
        "code.scribe.validate-fileboss-security",
        "code.sigma.validate-fileboss-security",
        "docs.monolith.validate-integrity",
        "analysis.monolith.estate-health",
    }
    action = actions["code.validate-governance"]
    assert action["domain"] == "code"
    assert action["canonicalAction"] == "code.validate-governance"
    assert action["targetRepository"] == "GlacierEQ/monolith"
    assert action["adapter"] == "monolith_ip_governance"
    assert action["receiptRoot"] == "receipts/code"
    assert action["receiptPattern"] == registry.expected_receipt_pattern("code")

    tool_system = actions["code.tool-system.validate"]
    assert tool_system["targetRepository"] == "GlacierEQ/computer-user"
    assert tool_system["adapter"] == "tool_system_validate"
    assert tool_system["tokenProfile"] == "private-source-read"

    atlases = actions["code.monolith.validate-atlases"]
    assert atlases["adapter"] == "test"
    assert atlases["receiptRoot"] == "receipts/code"

    operator_code = actions["code.fileboss.validate-operator-code-bridge"]
    assert operator_code["adapter"] == "fileboss_operator_code_validate"
    assert operator_code["targetRepository"] == "GlacierEQ/FILEBOSS"
    assert operator_code["receiptRoot"] == "receipts/code"

    scribe_security = actions["code.scribe.validate-fileboss-security"]
    assert scribe_security["adapter"] == "fileboss_security_validate"
    assert scribe_security["targetRepository"] == "GlacierEQ/scribe-multimodal-master"
    assert scribe_security["receiptRoot"] == "receipts/code"

    sigma_security = actions["code.sigma.validate-fileboss-security"]
    assert sigma_security["adapter"] == "fileboss_security_validate"
    assert sigma_security["targetRepository"] == "GlacierEQ/sigma-file-manager"
    assert sigma_security["receiptRoot"] == "receipts/code"

    docs = actions["docs.monolith.validate-integrity"]
    assert docs["domain"] == "docs"
    assert docs["adapter"] == "validate"
    assert docs["receiptRoot"] == "receipts/docs"

    analysis = actions["analysis.monolith.estate-health"]
    assert analysis["domain"] == "analysis"
    assert analysis["adapter"] == "audit"
    assert analysis["receiptRoot"] == "receipts/analysis"


def test_tool_system_alias_resolves_to_canonical_code_action() -> None:
    canonical = registry.resolve_action(
        "code.tool-system.validate", requested_domain="code"
    )
    alias = registry.resolve_action(
        "tool-system-validate", requested_domain="code"
    )
    assert canonical["wasAlias"] is False
    assert alias["wasAlias"] is True
    assert alias["canonicalAction"] == canonical["canonicalAction"]
    assert alias["targetRepository"] == "GlacierEQ/computer-user"


def test_specialized_aliases_resolve_to_their_domains() -> None:
    aliases = {
        "monolith-validate-atlases": ("code.monolith.validate-atlases", "code"),
        "monolith-docs-integrity": ("docs.monolith.validate-integrity", "docs"),
        "monolith-estate-health": ("analysis.monolith.estate-health", "analysis"),
    }
    for alias, (canonical, domain) in aliases.items():
        resolved = registry.resolve_action(alias, requested_domain=domain)
        assert resolved["wasAlias"] is True
        assert resolved["canonicalAction"] == canonical
        assert resolved["domain"] == domain
        assert resolved["targetRepository"] == "GlacierEQ/monolith"


def test_tool_system_action_uses_closed_action_specific_schemas() -> None:
    action = registry.resolve_action("code.tool-system.validate")
    assert action["jobSchema"] == "tool-system-job-v1"
    assert action["resultSchema"] == "tool-system-result-v1"
    assert action["jobSchemaPath"].endswith("tool-system-job.schema.json")
    assert action["resultSchemaPath"].endswith("tool-system-result.schema.json")


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


def test_unknown_action_cannot_resolve() -> None:
    with pytest.raises(registry.RegistryError, match="not registered"):
        registry.resolve_action("code.not-real")


def test_indexed_planned_domain_action_stays_non_executable(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "actions-index.json",
        lambda value: value["canonicalActions"].update(
            {
                "docs.generate-pdf": {
                    "domain": "docs",
                    "status": "planned",
                    "catalog": "domains/docs/actions.json",
                }
            }
        ),
    )
    with pytest.raises(registry.RegistryError, match="unresolved canonical actions"):
        registry.resolve_action(
            "docs.generate-pdf",
            requested_domain="docs",
            root=root,
        )


def test_cross_domain_resolution_is_rejected() -> None:
    with pytest.raises(registry.RegistryError, match="does not own"):
        registry.resolve_action(
            "code.validate-governance", requested_domain="analysis"
        )


def test_code_token_profile_is_exact_and_read_only() -> None:
    action = registry.resolve_action("code.validate-governance")
    profile = action["tokenProfileContract"]
    assert profile["status"] == "active"
    assert profile["repositoryCount"] == 1
    assert profile["repositorySelection"] == "catalog-exact"
    assert profile["permissions"] == {"contents": "read"}
    assert profile["maximumLifetimeSeconds"] == 3600
    assert profile["persistCredentials"] is False
    assert profile["exposeCredentialToWorkload"] is False
    assert profile["sourceWrites"] == "forbidden"
    assert profile["resultWrites"] == "control-plane-receipt-only"
    assert profile["revocation"] == "automatic-at-job-completion"


def test_all_active_domains_use_the_same_read_only_ceiling() -> None:
    for action_name in (
        "code.monolith.validate-atlases",
        "code.fileboss.validate-operator-code-bridge",
        "code.scribe.validate-fileboss-security",
        "code.sigma.validate-fileboss-security",
        "docs.monolith.validate-integrity",
        "analysis.monolith.estate-health",
    ):
        profile = registry.resolve_action(action_name)["tokenProfileContract"]
        assert profile["permissions"] == {"contents": "read"}
        assert profile["repositoryCount"] == 1
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


def test_repository_paths_reject_live_symlink_components(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "domains").symlink_to(outside, target_is_directory=True)
    with pytest.raises(registry.RegistryError, match="contains a symlink"):
        registry.safe_repository_path(
            tmp_path, "domains/code/actions.json", must_exist=False
        )


def test_repository_paths_reject_dangling_symlink_components(
    tmp_path: Path,
) -> None:
    (tmp_path / "domains").symlink_to(tmp_path / "missing-domain-root")
    with pytest.raises(registry.RegistryError, match="contains a symlink"):
        registry.safe_repository_path(
            tmp_path, "domains/code/actions.json", must_exist=False
        )


def test_receipt_namespace_is_executable_not_dead_configuration(
    tmp_path: Path,
) -> None:
    root = copy_registry_fixture(tmp_path)
    receipt = registry.receipt_path_for(
        "code.validate-governance",
        "DomainJob01",
        requested_domain="code",
        root=root,
    )
    assert receipt == root / "receipts" / "code" / "DomainJob01.json"

    with pytest.raises(registry.RegistryError, match="job_id is invalid"):
        registry.receipt_path_for(
            "code.validate-governance",
            "../../escape",
            root=root,
        )


def test_each_domain_receipt_namespace_is_isolated(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    expected = {
        "code.monolith.validate-atlases": root / "receipts" / "code" / "DomainJob01.json",
        "code.fileboss.validate-operator-code-bridge": root / "receipts" / "code" / "DomainJob01.json",
        "code.scribe.validate-fileboss-security": root / "receipts" / "code" / "DomainJob01.json",
        "code.sigma.validate-fileboss-security": root / "receipts" / "code" / "DomainJob01.json",
        "docs.monolith.validate-integrity": root / "receipts" / "docs" / "DomainJob01.json",
        "analysis.monolith.estate-health": root / "receipts" / "analysis" / "DomainJob01.json",
    }
    for action, path in expected.items():
        assert registry.receipt_path_for(action, "DomainJob01", root=root) == path


def test_disabled_action_index_entry_fails_closed(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "actions-index.json",
        lambda value: value["canonicalActions"]["code.validate-governance"].update(
            {"status": "disabled"}
        ),
    )
    with pytest.raises(registry.RegistryError, match="not active in the action index"):
        registry.validate_registry(root)


def test_action_index_catalog_rebinding_fails_closed(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "actions-index.json",
        lambda value: value["canonicalActions"]["code.validate-governance"].update(
            {"catalog": "domains/analysis/actions.json"}
        ),
    )
    with pytest.raises(registry.RegistryError, match="action-index catalog mismatch"):
        registry.validate_registry(root)


def test_disabled_token_profile_fails_closed(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "domains" / "code" / "token-profiles.json",
        lambda value: value["profiles"]["private-source-read"].update(
            {"status": "disabled"}
        ),
    )
    with pytest.raises(registry.RegistryError, match="token profile is not active"):
        registry.validate_registry(root)


def test_malformed_token_profile_name_returns_registry_error(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "domains" / "code" / "actions.json",
        lambda value: value["actions"]["code.validate-governance"].update(
            {"tokenProfile": ["private-source-read"]}
        ),
    )
    with pytest.raises(registry.RegistryError, match="profile name is invalid"):
        registry.validate_registry(root)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "maximumLifetimeSeconds",
            3601,
            "lifetime exceeds the one-hour ceiling",
        ),
        (
            "resultWrites",
            "source-repository-write",
            "result writes exceed the receipt-only boundary",
        ),
        (
            "revocation",
            "manual",
            "token revocation is not automatic",
        ),
    ],
)
def test_token_profile_ceiling_widening_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "domains" / "code" / "token-profiles.json",
        lambda data: data["profiles"]["private-source-read"].update(
            {field: value}
        ),
    )
    with pytest.raises(registry.RegistryError, match=message):
        registry.validate_registry(root)


def test_active_domain_requires_active_receipt_namespace(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "receipt-namespaces.json",
        lambda value: value["namespaces"]["code"].update(
            {"status": "reserved"}
        ),
    )
    with pytest.raises(registry.RegistryError, match="status is not active"):
        registry.validate_registry(root)


def test_receipt_pattern_drift_fails_closed(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "receipt-namespaces.json",
        lambda value: value["namespaces"]["code"].update(
            {"pattern": "^receipts/code/.+\\.json$"}
        ),
    )
    with pytest.raises(registry.RegistryError, match="pattern is not canonical"):
        registry.validate_registry(root)


def test_registry_constraint_weakening_fails_closed(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "domains.json",
        lambda value: value["constraints"].update(
            {"callerSelectedRepositories": True}
        ),
    )
    with pytest.raises(registry.RegistryError, match="constraints are missing or weakened"):
        registry.validate_registry(root)


@pytest.mark.parametrize("reserved", ["domain", "canonicalAction", "receiptRoot"])
def test_action_catalog_cannot_override_validated_identity(
    tmp_path: Path,
    reserved: str,
) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "domains" / "code" / "actions.json",
        lambda value: value["actions"]["code.validate-governance"].update(
            {reserved: "attacker-controlled"}
        ),
    )
    with pytest.raises(registry.RegistryError, match="reserved identity fields"):
        registry.validate_registry(root)


def test_alias_cannot_shadow_a_canonical_action(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    mutate_json(
        root / "registry" / "actions-index.json",
        lambda value: value["aliases"].update(
            {
                "code.validate-governance": {
                    "canonicalAction": "code.validate-governance",
                    "status": "temporary",
                    "removeAfter": "never",
                }
            }
        ),
    )
    with pytest.raises(registry.RegistryError, match="aliases shadow canonical actions"):
        registry.validate_registry(root)


def test_malformed_schema_fails_before_action_activation(tmp_path: Path) -> None:
    root = copy_registry_fixture(tmp_path)
    (root / "domains" / "code" / "schemas" / "job.schema.json").write_text(
        "{not-json\n", encoding="utf-8"
    )
    with pytest.raises(registry.RegistryError, match="could not be loaded"):
        registry.validate_registry(root)
