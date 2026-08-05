#!/usr/bin/env python3
"""Static fail-closed verification for the APEX Runner Bridge contract."""
from __future__ import annotations

import json
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "github-app" / "app-manifest.json"
CONTRACT = ROOT / "github-app" / "bridge-contract.json"
ACTIVATION = ROOT / "github-app" / "ACTIVATION.md"
BOOTSTRAP = ROOT / "github-app" / "bootstrap_apex_github_app.py"
WINDOWS_LAUNCHER = ROOT / "START_APEX_RUNNER_BRIDGE.cmd"
POWERSHELL_LAUNCHER = ROOT / "github-app" / "start_apex_runner_bridge.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "apex-github-app-bridge-canary.yml"
CANARY = ROOT / "scripts" / "github_app_bridge_canary.py"
PINNED_TOKEN_ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
PINNED_CHECKOUT = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
EXPECTED_REPOSITORIES = [
    "GlacierEQ/mastermind",
    "GlacierEQ/llm-runner-teams",
    "GlacierEQ/monolith",
    "GlacierEQ/MEGA-PDF",
]


def fail(message: str) -> None:
    print(f"APP_BRIDGE_CONTRACT_BLOCK: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    required_paths = (
        MANIFEST,
        CONTRACT,
        ACTIVATION,
        BOOTSTRAP,
        WINDOWS_LAUNCHER,
        POWERSHELL_LAUNCHER,
        WORKFLOW,
        CANARY,
    )
    for path in required_paths:
        if not path.is_file():
            fail(f"missing required bridge file: {path.relative_to(ROOT)}")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    activation = ACTIVATION.read_text(encoding="utf-8")
    launcher = WINDOWS_LAUNCHER.read_text(encoding="utf-8")
    powershell = POWERSHELL_LAUNCHER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    if manifest.get("public") is not False:
        fail("GitHub App must remain owner-only")
    hook = manifest.get("hook_attributes")
    if not isinstance(hook, dict) or hook.get("active") is not False:
        fail("GitHub App webhooks must remain inactive")
    if manifest.get("default_events") != []:
        fail("GitHub App must not subscribe to webhook events")
    if manifest.get("default_permissions") != {"contents": "write"}:
        fail("GitHub App registration must grant only contents:write")
    if manifest.get("request_oauth_on_install") is not False:
        fail("OAuth user authorization must remain disabled")

    if contract.get("status") != "automated_bootstrap_ready_not_activated":
        fail("bridge status must distinguish bootstrap readiness from live activation")
    activation_contract = contract.get("canonical_activation")
    if not isinstance(activation_contract, dict):
        fail("canonical activation contract is missing")
    if activation_contract.get("windows_launcher") != "START_APEX_RUNNER_BRIDGE.cmd":
        fail("Windows launcher drifted")
    if activation_contract.get("manual_credential_handling") is not False:
        fail("manual credential handling must remain forbidden")
    if activation_contract.get("completion_requires_verified_private_receipt") is not True:
        fail("activation must require a verified private receipt")

    app = contract.get("app") if isinstance(contract.get("app"), dict) else {}
    if app.get("owner_login") != "GlacierEQ" or app.get("owner_id") != 194243768:
        fail("bridge owner identity drifted")
    if app.get("installation_repository_selection") != "selected":
        fail("App installation must remain selected-repositories only")
    if contract.get("installation_repositories") != EXPECTED_REPOSITORIES:
        fail("exact installation repository allowlist drifted")

    stored = contract.get("stored_configuration")
    if not isinstance(stored, dict):
        fail("stored configuration contract is missing")
    if stored.get("manual_transport_forbidden") is not True:
        fail("manual Client ID or PEM transport must remain forbidden")

    activation_fragments = [
        "START_APEX_RUNNER_BRIDGE.cmd",
        "python github-app/bootstrap_apex_github_app.py",
        "The owner does not generate, view, copy, paste, store, or transmit the key.",
    ]
    for fragment in activation_fragments:
        if fragment not in activation:
            fail(f"activation invariant missing: {fragment}")

    launcher_fragments = [
        "start_apex_runner_bridge.ps1",
        "No PEM key will be shown, copied, pasted, or stored manually.",
        "APEX RUNNER BRIDGE FAILED CLOSED",
    ]
    for fragment in launcher_fragments:
        if fragment not in launcher:
            fail(f"Windows launcher invariant missing: {fragment}")

    powershell_fragments = [
        "'auth', 'login', '--web'",
        "bootstrap_apex_github_app.py",
        "No private key is displayed, copied, pasted, written to disk, or transported through chat.",
    ]
    for fragment in powershell_fragments:
        if fragment not in powershell:
            fail(f"PowerShell launcher invariant missing: {fragment}")

    required_fragments = [
        PINNED_TOKEN_ACTION,
        PINNED_CHECKOUT,
        "permission-contents: write",
        "permission-contents: read",
        "repositories: GlacierEQ/llm-runner-teams",
        "APEX_RUNNER_APP_CLIENT_ID",
        "APEX_RUNNER_APP_PRIVATE_KEY",
        "persist-credentials: false",
        "github_app_bridge_canary.py verify-and-claim",
        "github_app_bridge_canary.py complete",
    ]
    for fragment in required_fragments:
        if fragment not in workflow:
            fail(f"workflow invariant missing: {fragment}")

    forbidden_fragments = [
        "secrets.APEX_PRIVATE_READ_TOKEN",
        "secrets.APEX_CONTROL_TOKEN",
        "secrets.GH_PAT",
        "permission-actions:",
        "permission-administration:",
        "permission-secrets:",
        "permission-workflows:",
        "skip-token-revoke: true",
        "runs-on: self-hosted",
    ]
    for fragment in forbidden_fragments:
        if fragment in workflow:
            fail(f"forbidden workflow capability present: {fragment}")

    token_uses = re.findall(r"uses:\s*(actions/create-github-app-token@[^\s]+)", workflow)
    if token_uses != [PINNED_TOKEN_ACTION, PINNED_TOKEN_ACTION]:
        fail("workflow must mint exactly two tokens from the immutable approved action revision")

    private_key_references = workflow.count("secrets.APEX_RUNNER_APP_PRIVATE_KEY")
    if private_key_references != 3:
        fail("private key reference count drifted from configuration check plus two minting steps")

    for python_path in (CANARY, BOOTSTRAP):
        try:
            py_compile.compile(str(python_path), doraise=True)
        except py_compile.PyCompileError as exc:
            fail(f"bridge Python file does not compile: {python_path.name}: {exc.msg}")

    print(json.dumps({
        "status": "pass",
        "app": manifest.get("name"),
        "permissions": manifest.get("default_permissions"),
        "installation_repositories": EXPECTED_REPOSITORIES,
        "windows_launcher": str(WINDOWS_LAUNCHER.relative_to(ROOT)),
        "manual_credential_handling": False,
        "token_profiles": 2,
        "token_action_revision": PINNED_TOKEN_ACTION.rsplit("@", 1)[1],
        "pat_fallback": False,
        "webhooks": False,
        "oauth": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
