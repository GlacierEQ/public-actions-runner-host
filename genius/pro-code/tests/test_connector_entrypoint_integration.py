from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_precommit_requires_external_trust_root() -> None:
    source = read(REPO / ".pre-commit-config.yaml")
    assert 'test -n "${AKOS_POLICY_SHA256:-}"' in source
    assert "export AKOS_POLICY_SHA256=" not in source
    assert "always_run: true" in source


def test_workflow_uses_protected_secret_and_covers_entrypoints() -> None:
    source = read(ROOT / "smithery_control_plane/ci/akos-connector-policy.yml")
    assert "AKOS_POLICY_SHA256: ${{ secrets.AKOS_POLICY_SHA256 }}" in source
    assert "Verify protected trust root is available" in source
    for path in ("remote_mcp_server.py", "actor_violations_connector.py", "apex_orchestrator.py"):
        assert path in source


def test_workflow_sidecar_matches_exact_bytes() -> None:
    workflow = ROOT / "smithery_control_plane/ci/akos-connector-policy.yml"
    expected = read(workflow.with_suffix(".yml.sha256")).split()[0]
    assert sha256(workflow.read_bytes()).hexdigest() == expected


def test_remote_entrypoint_probes_and_validates_raw_result() -> None:
    source = read(ROOT / "remote_mcp_server.py")
    assert "async def _probe_tool_route" in source
    assert "validated = read_outcome(raw_result)" in source
    assert "execute_mcp_tool(name, args, probe, callback)" in source
    assert "ApexFileBossOrchestrator" in source
    assert "APEXOrchestrator" not in source


def test_actor_entrypoint_requires_live_probe() -> None:
    source = read(ROOT / "casebuilder/services/actor_violations_connector.py")
    assert "async def _probe_apex" in source
    assert "self._probe_apex" in source


def test_mcp_config_contains_only_runtime_placeholders() -> None:
    config = json.loads(read(ROOT / "mcp_config.json"))
    assert config["mcpServers"]["memory-plugin-a"]["auth"]["token"] == "${MEMORY_PLUGIN_TOKEN_A}"
    assert config["mcpServers"]["memory-plugin-b"]["auth"]["token"] == "${MEMORY_PLUGIN_TOKEN_B}"
    env = config["mcpServers"]["fileboss-local"]["env"]
    assert {"AKOS_POLICY_SHA256", "AKOS_ATTESTATION_HMAC_KEY", "AKOS_TENANT_ALIAS"}.issubset(env)


def test_deployment_bootstrap_is_fail_closed() -> None:
    railway = json.loads(read(REPO / "genius/shared/ops/paas/railway-mcp.json"))
    assert railway["deploy"]["preDeployCommand"].endswith("bootstrap_check.py")
    bootstrap = read(ROOT / "smithery_control_plane/runtime/bootstrap_check.py")
    for name in ("AKOS_POLICY_SHA256", "AKOS_ATTESTATION_HMAC_KEY", "AKOS_TENANT_ALIAS"):
        assert name in bootstrap


def test_bootstrap_document_names_secrets_without_values() -> None:
    source = read(ROOT / "smithery_control_plane/docs/AKOS_GATEWAY_BOOTSTRAP.md")
    for name in ("AKOS_POLICY_SHA256", "AKOS_ATTESTATION_HMAC_KEY", "AKOS_TENANT_ALIAS"):
        assert name in source
    assert "runtime secret only" in source.lower()
