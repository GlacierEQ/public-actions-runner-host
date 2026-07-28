from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import stat

import pytest

from smithery_control_plane.runtime.deployment_bootstrap import (
    DeploymentBootstrapError,
    configure_deployment_runtime,
    deployment_runtime_values,
    derive_route_tenant_alias,
)


def policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.json"
    path.write_bytes(b'{"version":"test"}')
    return path


def test_deployment_bootstrap_preserves_external_trust_root(tmp_path: Path) -> None:
    path = policy(tmp_path)
    values = deployment_runtime_values(
        {
            "AKOS_POLICY_SHA256": sha256(path.read_bytes()).hexdigest(),
            "AKOS_TENANT_ALIAS": "tenant-fixed",
            "AKOS_ALLOW_EPHEMERAL_ATTESTATION_KEY": "1",
        },
        policy_path=path,
        token_hex=lambda count: "ab" * count,
    )
    assert values["AKOS_POLICY_SHA256"] == sha256(path.read_bytes()).hexdigest()
    assert values["AKOS_ATTESTATION_HMAC_KEY"] == "ab" * 48
    assert values["AKOS_TENANT_ALIAS"] == "tenant-fixed"
    assert values["AKOS_REPAIR_QUEUE"].startswith("/tmp/fileboss/")


def test_deployment_bootstrap_refuses_self_generated_policy_root(tmp_path: Path) -> None:
    with pytest.raises(DeploymentBootstrapError, match="AKOS_POLICY_SHA256"):
        deployment_runtime_values(
            {
                "AKOS_TENANT_ALIAS": "tenant-fixed",
                "AKOS_ALLOW_EPHEMERAL_ATTESTATION_KEY": "1",
            },
            policy_path=policy(tmp_path),
        )


def test_ephemeral_hmac_requires_explicit_opt_in(tmp_path: Path) -> None:
    env = {
        "AKOS_POLICY_SHA256": "f" * 64,
        "AKOS_TENANT_ALIAS": "tenant-fixed",
    }
    with pytest.raises(DeploymentBootstrapError, match="ATTESTATION_HMAC_KEY"):
        deployment_runtime_values(env, policy_path=policy(tmp_path))


def test_protected_hmac_is_preserved(tmp_path: Path) -> None:
    values = deployment_runtime_values(
        {
            "AKOS_POLICY_SHA256": "f" * 64,
            "AKOS_ATTESTATION_HMAC_KEY": "protected",
            "AKOS_TENANT_ALIAS": "tenant-fixed",
        },
        policy_path=policy(tmp_path),
        token_hex=lambda count: (_ for _ in ()).throw(AssertionError("must not generate")),
    )
    assert values["AKOS_ATTESTATION_HMAC_KEY"] == "protected"


def test_remote_upstream_requires_identity_material() -> None:
    with pytest.raises(DeploymentBootstrapError):
        derive_route_tenant_alias({"ASPEN_GROVE_BASE_URL": "https://apex.example"})
    alias = derive_route_tenant_alias(
        {
            "ASPEN_GROVE_BASE_URL": "https://apex.example",
            "ASPEN_GROVE_API_KEY": "secret-value",
        }
    )
    assert alias == "apex-key-" + sha256(b"api-key:secret-value").hexdigest()[:20]


def test_tenant_derivation_requires_explicit_opt_in(tmp_path: Path) -> None:
    env = {
        "AKOS_POLICY_SHA256": "f" * 64,
        "AKOS_ATTESTATION_HMAC_KEY": "protected",
        "ASPEN_GROVE_BASE_URL": "http://localhost:7000",
    }
    with pytest.raises(DeploymentBootstrapError, match="AKOS_TENANT_ALIAS"):
        deployment_runtime_values(env, policy_path=policy(tmp_path))
    env["AKOS_DERIVE_TENANT_ALIAS"] = "1"
    values = deployment_runtime_values(env, policy_path=policy(tmp_path))
    assert values["AKOS_TENANT_ALIAS"].startswith("apex-local-")


def test_blank_placeholders_are_replaced_in_environment(tmp_path: Path) -> None:
    repair = tmp_path / "private" / "queue.jsonl"
    env = {
        "AKOS_POLICY_SHA256": "f" * 64,
        "AKOS_ATTESTATION_HMAC_KEY": "   ",
        "AKOS_TENANT_ALIAS": "tenant-fixed",
        "AKOS_ALLOW_EPHEMERAL_ATTESTATION_KEY": "1",
        "AKOS_REPAIR_QUEUE": str(repair),
    }
    values = configure_deployment_runtime(env)
    assert env["AKOS_ATTESTATION_HMAC_KEY"] == values["AKOS_ATTESTATION_HMAC_KEY"]
    assert env["AKOS_ATTESTATION_HMAC_KEY"].strip()
    assert env["AKOS_REPAIR_QUEUE"] == str(repair)


def test_configure_runtime_repairs_directory_and_file_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o755)
    repair = parent / "queue.jsonl"
    repair.write_text("", encoding="utf-8")
    repair.chmod(0o644)
    env = {
        "AKOS_POLICY_SHA256": "f" * 64,
        "AKOS_ATTESTATION_HMAC_KEY": "protected",
        "AKOS_TENANT_ALIAS": "tenant-fixed",
        "AKOS_REPAIR_QUEUE": str(repair),
    }
    configure_deployment_runtime(env)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(repair.stat().st_mode) == 0o600


def test_configure_runtime_rejects_symlink_queue(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "queue.jsonl"
    link.symlink_to(target)
    env = {
        "AKOS_POLICY_SHA256": "f" * 64,
        "AKOS_ATTESTATION_HMAC_KEY": "protected",
        "AKOS_TENANT_ALIAS": "tenant-fixed",
        "AKOS_REPAIR_QUEUE": str(link),
    }
    with pytest.raises(DeploymentBootstrapError, match="must not be a symlink"):
        configure_deployment_runtime(env)
