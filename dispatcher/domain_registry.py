#!/usr/bin/env python3
"""Load and validate the purpose-built runner domain registry.

The registry is data-driven and fail-closed: only active domains and explicitly
registered actions resolve. Callers may select a registered action, but they may
not select its adapter, repository, token profile, or receipt namespace.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
ACTION = re.compile(r"^[a-z][a-z0-9-]{1,31}\.[a-z][a-z0-9-]{1,63}$")
ADAPTER = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
REPOSITORY = re.compile(r"^GlacierEQ/[A-Za-z0-9_.-]{1,100}$")
REQUIRED_DOMAIN_KEYS = {
    "status",
    "executionEnabled",
    "adapterRoot",
    "actionCatalog",
    "tokenProfiles",
    "jobSchema",
    "resultSchema",
    "receiptNamespace",
    "concurrencyPrefix",
}


class RegistryError(ValueError):
    """Raised when registry data is malformed, unsafe, or unresolved."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RegistryError(
            f"registry file could not be loaded: {path}: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise RegistryError(f"registry file must contain a JSON object: {path}")
    return value


def safe_repository_path(root: Path, relative: str, *, must_exist: bool) -> Path:
    """Resolve one normalized repository-relative path without allowing escape."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise RegistryError("registry path must be a non-empty POSIX relative path")
    lexical = Path(relative)
    if lexical.is_absolute() or any(part in {"", ".", ".."} for part in lexical.parts):
        raise RegistryError(f"unsafe registry path: {relative}")

    root = root.resolve()
    candidate = root.joinpath(*lexical.parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RegistryError(f"registry path escapes repository root: {relative}") from error

    current = root
    for part in lexical.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise RegistryError(f"registry path contains a symlink: {relative}")
    if must_exist and not candidate.exists():
        raise RegistryError(f"required registry path is missing: {relative}")
    return candidate


def _load_domains(root: Path) -> dict[str, Any]:
    data = _load_json(root / "registry" / "domains.json")
    if data.get("schema_version") != "1.0":
        raise RegistryError("unsupported domain registry schema_version")
    domains = data.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise RegistryError("domain registry must contain domains")
    return data


def _load_action_index(root: Path) -> dict[str, Any]:
    data = _load_json(root / "registry" / "actions-index.json")
    if data.get("schema_version") != "1.0":
        raise RegistryError("unsupported action-index schema_version")
    if not isinstance(data.get("canonicalActions"), dict):
        raise RegistryError("action index must contain canonicalActions")
    if not isinstance(data.get("aliases"), dict):
        raise RegistryError("action index must contain aliases")
    return data


def _load_receipt_namespaces(root: Path) -> dict[str, Any]:
    data = _load_json(root / "registry" / "receipt-namespaces.json")
    if data.get("schema_version") != "1.0":
        raise RegistryError("unsupported receipt-namespace schema_version")
    if not isinstance(data.get("namespaces"), dict):
        raise RegistryError("receipt registry must contain namespaces")
    return data


def validate_registry(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Validate all active domain contracts and return their canonical actions."""
    root = root.resolve()
    domain_data = _load_domains(root)
    action_index = _load_action_index(root)
    receipt_data = _load_receipt_namespaces(root)
    canonical_index = action_index["canonicalActions"]
    aliases = action_index["aliases"]
    namespaces = receipt_data["namespaces"]

    loaded: dict[str, dict[str, Any]] = {}
    seen_aliases: set[str] = set()

    for domain_name, contract in domain_data["domains"].items():
        if not isinstance(domain_name, str) or not DOMAIN.fullmatch(domain_name):
            raise RegistryError(f"invalid domain name: {domain_name!r}")
        if not isinstance(contract, dict):
            raise RegistryError(f"domain contract must be an object: {domain_name}")
        missing = sorted(REQUIRED_DOMAIN_KEYS - set(contract))
        if missing:
            raise RegistryError(
                f"domain {domain_name} is missing keys: {', '.join(missing)}"
            )

        status = contract.get("status")
        enabled = contract.get("executionEnabled")
        if status not in {"active", "planned", "disabled"}:
            raise RegistryError(f"domain {domain_name} has invalid status")
        if not isinstance(enabled, bool):
            raise RegistryError(f"domain {domain_name} executionEnabled must be boolean")
        if enabled != (status == "active"):
            raise RegistryError(
                f"domain {domain_name} executionEnabled conflicts with status"
            )
        if contract.get("concurrencyPrefix") != domain_name:
            raise RegistryError(
                f"domain {domain_name} concurrency prefix must equal the domain"
            )
        if contract.get("receiptNamespace") != f"receipts/{domain_name}":
            raise RegistryError(
                f"domain {domain_name} receipt namespace is not canonical"
            )

        namespace = namespaces.get(domain_name)
        if not isinstance(namespace, dict):
            raise RegistryError(f"domain {domain_name} has no receipt namespace")
        if namespace.get("root") != contract["receiptNamespace"]:
            raise RegistryError(f"domain {domain_name} receipt roots disagree")

        if status != "active":
            continue

        adapter_root = safe_repository_path(
            root, str(contract["adapterRoot"]), must_exist=True
        )
        catalog_path = safe_repository_path(
            root, str(contract["actionCatalog"]), must_exist=True
        )
        token_path = safe_repository_path(
            root, str(contract["tokenProfiles"]), must_exist=True
        )
        safe_repository_path(root, str(contract["jobSchema"]), must_exist=True)
        safe_repository_path(root, str(contract["resultSchema"]), must_exist=True)

        catalog = _load_json(catalog_path)
        if catalog.get("schema_version") != "1.0" or catalog.get("domain") != domain_name:
            raise RegistryError(f"domain {domain_name} action catalog identity mismatch")
        actions = catalog.get("actions")
        if not isinstance(actions, dict) or not actions:
            raise RegistryError(f"active domain {domain_name} has no actions")

        token_data = _load_json(token_path)
        if token_data.get("schema_version") != "1.0" or token_data.get("domain") != domain_name:
            raise RegistryError(f"domain {domain_name} token profile identity mismatch")
        profiles = token_data.get("profiles")
        if not isinstance(profiles, dict):
            raise RegistryError(f"domain {domain_name} token profiles are malformed")

        for action_name, action in actions.items():
            if not isinstance(action_name, str) or not ACTION.fullmatch(action_name):
                raise RegistryError(f"invalid canonical action: {action_name!r}")
            if not action_name.startswith(f"{domain_name}."):
                raise RegistryError(
                    f"action {action_name} is registered under the wrong domain"
                )
            if not isinstance(action, dict) or action.get("status") != "active":
                raise RegistryError(f"action {action_name} is not active")

            adapter = action.get("adapter")
            if not isinstance(adapter, str) or not ADAPTER.fullmatch(adapter):
                raise RegistryError(f"action {action_name} has an invalid adapter")
            adapter_path = safe_repository_path(
                root, str(action.get("adapterPath", "")), must_exist=True
            )
            try:
                adapter_path.resolve().relative_to(adapter_root.resolve())
            except ValueError as error:
                raise RegistryError(
                    f"action {action_name} adapter escapes its domain adapter root"
                ) from error

            repository = action.get("targetRepository")
            if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
                raise RegistryError(f"action {action_name} target repository is invalid")

            profile_name = action.get("tokenProfile")
            profile = profiles.get(profile_name)
            if not isinstance(profile, dict):
                raise RegistryError(f"action {action_name} token profile is missing")
            if profile.get("repositoryCount") != 1:
                raise RegistryError(f"action {action_name} token must target one repository")
            if profile.get("repositorySelection") != "catalog-exact":
                raise RegistryError(
                    f"action {action_name} repository selection is not catalog-exact"
                )
            if profile.get("permissions") != {"contents": "read"}:
                raise RegistryError(
                    f"action {action_name} token permissions exceed contents:read"
                )
            if profile.get("persistCredentials") is not False:
                raise RegistryError(f"action {action_name} may not persist credentials")
            if profile.get("exposeCredentialToWorkload") is not False:
                raise RegistryError(
                    f"action {action_name} may not expose its credential to the workload"
                )
            if profile.get("sourceWrites") != "forbidden":
                raise RegistryError(f"action {action_name} source writes are not forbidden")

            if action.get("receiptNamespace") != domain_name:
                raise RegistryError(f"action {action_name} receipt namespace mismatch")
            indexed = canonical_index.get(action_name)
            if not isinstance(indexed, dict) or indexed.get("domain") != domain_name:
                raise RegistryError(f"action {action_name} is absent from the action index")

            legacy_aliases = action.get("legacyAliases", [])
            if not isinstance(legacy_aliases, list):
                raise RegistryError(f"action {action_name} legacyAliases must be a list")
            for alias in legacy_aliases:
                if not isinstance(alias, str) or alias in seen_aliases:
                    raise RegistryError(f"invalid or duplicate action alias: {alias!r}")
                alias_entry = aliases.get(alias)
                if (
                    not isinstance(alias_entry, dict)
                    or alias_entry.get("canonicalAction") != action_name
                ):
                    raise RegistryError(f"alias {alias} does not bind to {action_name}")
                seen_aliases.add(alias)

            loaded[action_name] = {
                "domain": domain_name,
                "canonicalAction": action_name,
                **action,
                "tokenProfileContract": profile,
                "receiptRoot": namespace["root"],
            }

    dangling = sorted(set(canonical_index) - set(loaded))
    if dangling:
        raise RegistryError(
            f"action index contains unresolved canonical actions: {', '.join(dangling)}"
        )
    unknown_aliases = sorted(set(aliases) - seen_aliases)
    if unknown_aliases:
        raise RegistryError(
            f"action index contains unresolved aliases: {', '.join(unknown_aliases)}"
        )
    return loaded


def resolve_action(
    action: str, *, requested_domain: str | None = None, root: Path = ROOT
) -> dict[str, Any]:
    """Resolve a canonical action or temporary alias through the active registry."""
    if not isinstance(action, str) or not action:
        raise RegistryError("action is required")
    action_index = _load_action_index(root.resolve())
    alias_entry = action_index["aliases"].get(action)
    canonical = (
        alias_entry.get("canonicalAction")
        if isinstance(alias_entry, dict)
        else action
    )
    actions = validate_registry(root)
    entry = actions.get(canonical)
    if entry is None:
        raise RegistryError("action is not registered to an active domain")
    if requested_domain is not None and requested_domain != entry["domain"]:
        raise RegistryError("requested domain does not own the action")
    return {
        **entry,
        "requestedAction": action,
        "wasAlias": canonical != action,
    }
