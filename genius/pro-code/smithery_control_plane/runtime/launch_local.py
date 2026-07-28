"""Self-starting local stdio entrypoint for FILEBOSS MCP.

Production deployments still require externally provisioned AKOS secrets. This
launcher creates process-local trust only for an explicitly local MCP process.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit


def _local_tenant_alias() -> str:
    base_url = os.environ.get("ASPEN_GROVE_BASE_URL", "http://localhost:7000")
    parsed = urlsplit(base_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            "Local launcher only accepts a loopback ASPEN_GROVE_BASE_URL; "
            "use protected deployment secrets for remote routes"
        )
    material = f"loopback:{parsed.scheme}:{parsed.netloc}".encode("utf-8")
    return "apex-local-" + sha256(material).hexdigest()[:20]


def configure_local_runtime() -> None:
    project_root = Path(__file__).resolve().parents[2]
    policy = project_root / "smithery_control_plane" / "config" / "akos_connector_policy.json"
    repair_queue = Path.home() / ".fileboss" / "akos" / "repair_queue.jsonl"
    os.environ.setdefault("AKOS_POLICY_SHA256", sha256(policy.read_bytes()).hexdigest())
    os.environ.setdefault("AKOS_ATTESTATION_HMAC_KEY", secrets.token_hex(32))
    os.environ.setdefault("AKOS_TENANT_ALIAS", _local_tenant_alias())
    os.environ.setdefault("AKOS_REPAIR_QUEUE", str(repair_queue))
    os.environ.setdefault("FILEBOSS_ALLOWED_ROOTS", str(Path.home()))


async def serve_stdio() -> int:
    configure_local_runtime()
    from remote_mcp_server import _process_jsonrpc
    from smithery_control_plane.runtime.connector_gateway import default_gateway

    default_gateway()
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if line == "":
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = await _process_jsonrpc(request)
            if request.get("id") is None:
                continue
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(exc)},
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main() -> int:
    return asyncio.run(serve_stdio())


if __name__ == "__main__":
    raise SystemExit(main())
