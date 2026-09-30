#!/usr/bin/env python3
"""Register the APEX boot-core exact validator across runner source registries.

This is a deterministic branch-maintenance patcher. It updates only the explicit
runtime dispatcher, global action index, private-source authorization/receipt
namespace registry, and registry regression test. It never mutates result
projections or private source repositories.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION_ID = "code.apex-boot-core.validate"
SOURCE_REPO = "GlacierEQ/apex-boot-core"
RECEIPT_NAMESPACE = "code_apex_boot_core_validate"
TOKEN_PROFILE = "code_read_only"


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def patch_dispatcher() -> bool:
    path = ROOT / "scripts" / "action_face_catalog_runner.py"
    text = path.read_text(encoding="utf-8")
    changed = False

    import_line = (
        "from domains.code.adapters.apex_boot_core_validate import execute as "
        "run_apex_boot_core_validate\n"
    )
    anchor = (
        "from domains.code.adapters.fileboss_security_validate import run as "
        "run_fileboss_security_validate\n"
    )
    if import_line not in text:
        if anchor not in text:
            raise RuntimeError("dispatcher import anchor not found")
        text = text.replace(anchor, anchor + import_line, 1)
        changed = True

    dispatch = '''\n        if action_id == "code.apex-boot-core.validate":\n            result = run_apex_boot_core_validate(plan=plan)\n            write_json(result_path, result)\n            return 0 if result.get("status") == "passed" else 1\n'''
    if 'action_id == "code.apex-boot-core.validate"' not in text:
        anchor = '''\n        if action_id == "code.fileboss.security-validate":\n            result = run_fileboss_security_validate(plan=plan)\n            write_json(result_path, result)\n            return 0 if result.get("status") == "passed" else 1\n'''
        if anchor not in text:
            raise RuntimeError("dispatcher specialized-action anchor not found")
        text = text.replace(anchor, anchor + dispatch, 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def patch_action_index() -> bool:
    path = ROOT / "registry" / "actions-index.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    actions = data.get("actions")
    if not isinstance(actions, list):
        raise RuntimeError("registry/actions-index.json actions must be a list")
    if any(row.get("action") == ACTION_ID for row in actions if isinstance(row, dict)):
        return False
    actions.append(
        {
            "action": ACTION_ID,
            "namespace": RECEIPT_NAMESPACE,
            "pillar": "C",
            "privateSourceRead": True,
            "receiptNamespace": RECEIPT_NAMESPACE,
            "targetRepository": SOURCE_REPO,
            "tokenProfile": TOKEN_PROFILE,
        }
    )
    actions.sort(key=lambda row: str(row.get("action", "")))
    dump_json(path, data)
    return True


def patch_token_profiles() -> bool:
    path = ROOT / "config" / "token-profiles.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    namespaces = data.get("receipt_namespaces")
    if not isinstance(namespaces, list):
        raise RuntimeError("receipt_namespaces must be a list")
    if RECEIPT_NAMESPACE not in namespaces:
        namespaces.append(RECEIPT_NAMESPACE)
        namespaces.sort()
        changed = True

    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or TOKEN_PROFILE not in profiles:
        raise RuntimeError("code_read_only token profile missing")
    scope = profiles[TOKEN_PROFILE].get("authorization_scope")
    if not isinstance(scope, dict):
        raise RuntimeError("code_read_only authorization_scope missing")
    repos = scope.get("sourceRepositories")
    if not isinstance(repos, list):
        raise RuntimeError("code_read_only sourceRepositories must be a list")
    if SOURCE_REPO not in repos:
        repos.append(SOURCE_REPO)
        repos.sort()
        changed = True

    if changed:
        dump_json(path, data)
    return changed


def patch_registry_test() -> bool:
    path = ROOT / "tests" / "test_domain_registry.py"
    text = path.read_text(encoding="utf-8")
    changed = False
    expected_line = '        "code.apex-boot-core.validate",\n'
    if expected_line not in text:
        anchor = '        "code.ci.python",\n'
        if anchor not in text:
            raise RuntimeError("code action-set test anchor not found")
        text = text.replace(anchor, expected_line + anchor, 1)
        changed = True

    test_name = "def test_apex_boot_core_action_is_exact_private_read_only_validator"
    if test_name not in text:
        addition = '''\n\ndef test_apex_boot_core_action_is_exact_private_read_only_validator() -> None:\n    action = _action_map("code")["code.apex-boot-core.validate"]\n    assert action["targetRepository"] == "GlacierEQ/apex-boot-core"\n    assert action["sourceRepository"] == "GlacierEQ/apex-boot-core"\n    assert action["privateSourceRead"] is True\n    assert action["externalMutation"] is False\n    assert action["networkRequired"] is False\n    assert action["tokenProfile"] == "code_read_only"\n    assert action["receiptNamespace"] == "code_apex_boot_core_validate"\n    assert action["adapterPath"] == "domains/code/adapters/apex_boot_core_validate.py"\n    assert action["jobSchema"] == "domains/code/schemas/apex-boot-core-job.schema.json"\n    assert action["outputSchema"] == "domains/code/schemas/apex-boot-core-result.schema.json"\n'''
        text += addition
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def verify() -> None:
    domain = json.loads((ROOT / "domains" / "code" / "actions.json").read_text(encoding="utf-8"))
    rows = [row for row in domain.get("actions", []) if row.get("id") == ACTION_ID]
    if len(rows) != 1:
        raise RuntimeError("code action catalog must contain exactly one boot-core action")
    action = rows[0]
    if action.get("sourceRepository") != SOURCE_REPO:
        raise RuntimeError("boot-core action source repository drift")
    if action.get("tokenProfile") != TOKEN_PROFILE:
        raise RuntimeError("boot-core token profile drift")
    for rel in (
        action.get("adapterPath"),
        action.get("jobSchema"),
        action.get("outputSchema"),
    ):
        if not isinstance(rel, str) or not (ROOT / rel).is_file():
            raise RuntimeError(f"registered boot-core surface missing: {rel!r}")

    dispatcher = (ROOT / "scripts" / "action_face_catalog_runner.py").read_text(encoding="utf-8")
    if 'action_id == "code.apex-boot-core.validate"' not in dispatcher:
        raise RuntimeError("boot-core dispatcher branch missing")
    if "run_apex_boot_core_validate" not in dispatcher:
        raise RuntimeError("boot-core dispatcher import missing")

    index = json.loads((ROOT / "registry" / "actions-index.json").read_text(encoding="utf-8"))
    indexed = [row for row in index.get("actions", []) if row.get("action") == ACTION_ID]
    if len(indexed) != 1:
        raise RuntimeError("boot-core action index row missing or duplicated")

    token = json.loads((ROOT / "config" / "token-profiles.json").read_text(encoding="utf-8"))
    if RECEIPT_NAMESPACE not in token.get("receipt_namespaces", []):
        raise RuntimeError("boot-core receipt namespace missing")
    repos = token["profiles"][TOKEN_PROFILE]["authorization_scope"]["sourceRepositories"]
    if SOURCE_REPO not in repos:
        raise RuntimeError("boot-core source repo missing from code_read_only scope")


def main() -> int:
    changed = {
        "dispatcher": patch_dispatcher(),
        "action_index": patch_action_index(),
        "token_profiles": patch_token_profiles(),
        "registry_test": patch_registry_test(),
    }
    verify()
    print(json.dumps({"status": "VERIFIED", "action": ACTION_ID, "changed": changed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
