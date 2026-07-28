"""Deployment-only AKOS bootstrap with no committed credential values."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import secrets
from typing import Callable, Mapping, MutableMapping
from urllib.parse import urlsplit


class DeploymentBootstrapError(RuntimeError):
    pass


def derive_route_tenant_alias(environment: Mapping[str, str]) -> str:
    api_key = environment.get("ASPEN_GROVE_API_KEY", "").strip()
    if api_key:
        return "apex-key-" + sha256(f"api-key:{api_key}".encode("utf-8")).hexdigest()[:20]
    base_url = environment.get("ASPEN_GROVE_BASE_URL", "http://localhost:7000")
    parsed = urlsplit(base_url)
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        material = f"loopback:{parsed.scheme}:{parsed.netloc}".encode("utf-8")
        return "apex-local-" + sha256(material).hexdigest()[:20]
    raise DeploymentBootstrapError(
        "Remote ASPEN_GROVE_BASE_URL requires ASPEN_GROVE_API_KEY or explicit AKOS_TENANT_ALIAS"
    )


def deployment_runtime_values(
    environment: Mapping[str, str],
    *,
    policy_path: Path,
    token_hex: Callable[[int], str] = secrets.token_hex,
) -> dict[str, str]:
    if not policy_path.is_file():
        raise DeploymentBootstrapError(f"AKOS policy not found: {policy_path}")
    trusted_digest = environment.get("AKOS_POLICY_SHA256", "").strip()
    if not trusted_digest:
        raise DeploymentBootstrapError("AKOS_POLICY_SHA256 must come from protected deployment configuration")
    attestation_key = environment.get("AKOS_ATTESTATION_HMAC_KEY", "").strip()
    if not attestation_key:
        if environment.get("AKOS_ALLOW_EPHEMERAL_ATTESTATION_KEY", "").strip() != "1":
            raise DeploymentBootstrapError(
                "AKOS_ATTESTATION_HMAC_KEY is required unless ephemeral deployment signing is explicitly enabled"
            )
        attestation_key = token_hex(48)
    tenant_alias = environment.get("AKOS_TENANT_ALIAS", "").strip()
    if not tenant_alias:
        if environment.get("AKOS_DERIVE_TENANT_ALIAS", "").strip() != "1":
            raise DeploymentBootstrapError("AKOS_TENANT_ALIAS must come from protected deployment configuration")
        tenant_alias = derive_route_tenant_alias(environment)
    return {
        "AKOS_POLICY_SHA256": trusted_digest,
        "AKOS_ATTESTATION_HMAC_KEY": attestation_key,
        "AKOS_TENANT_ALIAS": tenant_alias,
        "AKOS_REPAIR_QUEUE": environment.get("AKOS_REPAIR_QUEUE", "").strip()
        or str(Path("/tmp/fileboss/akos_repair_queue.jsonl")),
        "FILEBOSS_ALLOWED_ROOTS": environment.get("FILEBOSS_ALLOWED_ROOTS", "").strip()
        or str(Path("/tmp/fileboss")),
    }


def configure_deployment_runtime(
    environment: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ if environment is None else environment
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "akos_connector_policy.json"
    )
    values = deployment_runtime_values(env, policy_path=policy_path)
    for name, value in values.items():
        env.setdefault(name, value)
    repair_parent = Path(env["AKOS_REPAIR_QUEUE"]).expanduser().parent
    repair_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return values
