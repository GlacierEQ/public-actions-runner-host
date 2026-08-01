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
    shutil.copytree(ROOT / "domains" / "code", tmp_path / "domains" / "code")
    return tmp_path


def mutate_json(path: Path, mutation: Callable[[dict], None]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_registry_validates_and_only_code_is_executable() -> None:
    actions = registry.validate_registry()
    assert set(actions) == {"code.validate-governance"}
    action = actions["code.validate-governance"]
    assert action["domain"] == "code"
    assert action["targetRepository"] == "GlacierEQ/monolith"
    assert action["adapter"] == "monolith_ip_governance"
    assert action["receiptRoot"] == "receipts/code"
    assert action["receiptPattern"] == registry.expected_receipt_pattern("code")


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


def test_repository_paths_cannot_escape_the_checkout(tmp_path: Path) -> None:
    with pytest.raises(registry.RegistryError, match="unsafe registry path"):
        registry.safe_repository_path(
            tmp_path, "../outside.json", must_exist=False
        )
    with pytest.raises(registry.RegistryError, match="POSIX relative"):
        registry.safe_repository_path(
            tmp_path, "domains\\code\\actions.json", must_exist=False
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
