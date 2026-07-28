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


def _assert_owned(path: Path) -> None:
    getuid = getattr(os, "geteuid", None)
    if callable(getuid) and path.stat().st_uid != getuid():
        raise DeploymentBootstrapError(f"Runtime path is not owned by this process user: {path}")


def _secure_repair_queue(queue: Path) -> None:
    parent = queue.parent
    if parent.is_symlink():
        raise DeploymentBootstrapError(f"Repair queue parent must not be a symlink: {parent}")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _assert_owned(parent)
    os.chmod(parent, 0o700)

    if queue.is_symlink():
        raise DeploymentBootstrapError(f"Repair queue must not be a symlink: {queue}")
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(queue, flags, 0o600)
    except OSError as exc:
        raise DeploymentBootstrapError(f"Cannot secure repair queue {queue}: {exc}") from exc
    try:
        getuid = getattr(os, "geteuid", None)
        stat = os.fstat(descriptor)
        if callable(getuid) and stat.st_uid != getuid():
            raise DeploymentBootstrapError(
                f"Repair queue is not owned by this process user: {queue}"
            )
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


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
        env[name] = value
    os.umask(0o077)
    queue = Path(env["AKOS_REPAIR_QUEUE"]).expanduser()
    _secure_repair_queue(queue)
    return values
