"""
Actor Violations Connector
==========================
Bridges the Perplexity "Actor Violations" Space output directly into the
FILEBOSS APEX pipeline for case 1FDV-23-0001009.

Flow:
  1. Receive actor_violations JSON (from Space prompt output)
  2. Store across Memory Triad (Memory Plugin + Supermemory + Mem0)
  3. Delegate RICO/§1983 pattern analysis to Operator Code MCP
  4. Index in Notion and sync to GitHub

Usage:
  from casebuilder.services.actor_violations_connector import ActorViolationsConnector
  connector = ActorViolationsConnector()
  result = await connector.ingest(actor_violations_json)
"""

import os
import json
import logging
import httpx
from urllib.parse import urlsplit
from datetime import datetime
from hashlib import sha256

from smithery_control_plane.runtime.connector_gateway import (
    ExecutionOutcome,
    ProbeEvidence,
    execute_actor_operation,
    read_outcome,
)
from smithery_control_plane.runtime.connector_policy import receipt_to_dict

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
APEX_BASE        = os.getenv("APEX_BASE_URL", "http://localhost:8000")
CASE_ID          = "1FDV-23-0001009"
BUCKET           = "actor_violations_master"
CONTEXT_GLOBAL   = os.getenv("CONTEXT_GLOBAL", "")
CONTEXT_DIRECT   = os.getenv("CONTEXT_DIRECT", "")

# ── Connector ────────────────────────────────────────────────────────────────
class ActorViolationsConnector:
    """
    End-to-end connector: Actor Violations Space → APEX Memory Triad
    → Cascade AI analysis → Notion index → GitHub sync.
    """

    def __init__(self, base_url: str = APEX_BASE):
        self.base_url = base_url
        self.session_tag = f"AVC-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    def _local_route_alias(self) -> str:
        parsed = urlsplit(self.base_url)
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            material = f"actor-loopback:{parsed.scheme}:{parsed.netloc}".encode("utf-8")
            return "actor-local-" + sha256(material).hexdigest()[:20]
        return ""

    async def _probe_apex(self) -> ProbeEvidence:
        """Contact APEX and bind policy identity to the live route response."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/apex/health")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    return ProbeEvidence(False, "", {"reason": "invalid_health_payload"})
                if payload.get("isError") or payload.get("error"):
                    return ProbeEvidence(False, "", {"reason": "health_error_payload"})
                status = str(payload.get("status", "ok")).strip().lower()
                if status in {"error", "failed", "failure", "unauthorized", "forbidden"}:
                    return ProbeEvidence(False, "", {"reason": "health_failed"})
                alias = str(
                    response.headers.get("X-AKOS-Tenant-Alias")
                    or payload.get("authenticated_tenant_alias")
                    or payload.get("tenant_alias")
                    or self._local_route_alias()
                ).strip()
                return ProbeEvidence(
                    bool(alias),
                    alias,
                    {"status_code": str(response.status_code)},
                )
        except Exception as exc:
            return ProbeEvidence(False, "", {"reason": type(exc).__name__})


    async def ingest(self, actor_json: dict, version: str = "1.0") -> dict:
        """Authorize the full ingestion pipeline before any external write."""

        async def callback():
            result = await self._ingest_unchecked(actor_json, version)
            payload_bytes = json.dumps(
                actor_json, sort_keys=True, ensure_ascii=False
            ).encode("utf-8")
            payload_hash = sha256(payload_bytes).hexdigest()
            # Current route authority deliberately advertises no write completion
            # contract, so AKOS blocks before this callback until readback,
            # retention, registry-update, and audit proofs are implemented.
            return ExecutionOutcome(
                result=result,
                completion_proofs=frozenset(),
                artifact_hashes={"actor_payload_sha256": payload_hash},
                artifact_bytes={"actor_payload_sha256": payload_bytes},
            )

        result, receipt = await execute_actor_operation(
            "ingest",
            {"version": version},
            self._probe_apex,
            callback,
        )
        response = dict(result)
        response["akos_receipt"] = receipt_to_dict(receipt)
        return response

    async def _ingest_unchecked(self, actor_json: dict, version: str = "1.0") -> dict:
        """
        Full pipeline ingestion of an actor violations JSON object.

        Args:
            actor_json: Completed actor violations JSON (matches APEX schema)
            version:    Schema version tag for traceability

        Returns:
            dict with keys: store_result, analysis_result, errors
        """
        results = {"session": self.session_tag, "errors": []}
        payload_str = json.dumps(actor_json, ensure_ascii=False)

        async with httpx.AsyncClient(timeout=60) as client:

            # ── Step 1: Store in Memory Triad ────────────────────────────────
            try:
                store_resp = await client.post(
                    f"{self.base_url}/apex/memory/store",
                    json={
                        "content": payload_str,
                        "bucket": BUCKET,
                        "metadata": {
                            **{
                                "case": CASE_ID,
                                "type": "actor_violation_map",
                                "version": version,
                                "session": self.session_tag,
                                "ingested_at": datetime.utcnow().isoformat(),
                            },
                            **(
                                {"context_global": CONTEXT_GLOBAL}
                                if CONTEXT_GLOBAL
                                else {}
                            ),
                            **(
                                {"context_direct": CONTEXT_DIRECT}
                                if CONTEXT_DIRECT
                                else {}
                            ),
                        }
                    }
                )
                store_resp.raise_for_status()
                results["store_result"] = store_resp.json()
                logger.info(f"[{self.session_tag}] Memory Triad store: OK")
            except Exception as e:
                err = f"Memory store failed: {e}"
                results["errors"].append(err)
                logger.error(err)

            # ── Step 2: Delegate RICO / §1983 pattern analysis ───────────────
            try:
                delegate_resp = await client.post(
                    f"{self.base_url}/apex/delegate",
                    json={
                        "task": (
                            "Analyze actor violations JSON for case 1FDV-23-0001009. "
                            "Identify: (1) RICO enterprise patterns and predicate acts; "
                            "(2) 42 U.S.C. §1983 color-of-law violations per actor; "
                            "(3) due process / equal protection deprivations; "
                            "(4) coordinated actor conduct across systemic themes; "
                            "(5) federal escalation readiness per violation. "
                            "Output structured findings mapped to violation_ids."
                        ),
                        "context": {
                            **{
                                "case_id": CASE_ID,
                                "bucket": BUCKET,
                                "strategy": "federal_escalation",
                            },
                            **(
                                {"context_global": CONTEXT_GLOBAL}
                                if CONTEXT_GLOBAL
                                else {}
                            ),
                            **(
                                {"context_direct": CONTEXT_DIRECT}
                                if CONTEXT_DIRECT
                                else {}
                            ),
                        },
                        "priority": "high"
                    }
                )
                delegate_resp.raise_for_status()
                results["analysis_result"] = delegate_resp.json()
                logger.info(f"[{self.session_tag}] Operator Code delegation: OK")
            except Exception as e:
                err = f"Delegation failed: {e}"
                results["errors"].append(err)
                logger.error(err)

            # ── Step 3: APEX batch search to confirm storage ─────────────────
            try:
                search_resp = await client.post(
                    f"{self.base_url}/apex/search",
                    json={
                        "query": f"actor violations {CASE_ID}",
                        "limit": 5
                    }
                )
                search_resp.raise_for_status()
                results["verification"] = search_resp.json()
                logger.info(f"[{self.session_tag}] Verification search: OK")
            except Exception as e:
                results["errors"].append(f"Verification search failed: {e}")

        results["status"] = "success" if not results["errors"] else "partial"
        return results

    async def recall(
        self,
        query: str = f"actor violations {CASE_ID}",
        limit: int = 10,
    ) -> dict:
        """Recall only through the AKOS read gateway."""

        async def callback():
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.base_url}/apex/memory/recall",
                    params={"query": query, "bucket": BUCKET, "limit": limit},
                )
                resp.raise_for_status()
                return read_outcome(resp.json())

        result, receipt = await execute_actor_operation(
            "recall",
            {"limit": limit},
            self._probe_apex,
            callback,
        )
        response = dict(result)
        response["akos_receipt"] = receipt_to_dict(receipt)
        return response

    async def health(self) -> dict:
        """Check APEX health through the AKOS read gateway."""

        async def callback():
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/apex/health")
                resp.raise_for_status()
                return read_outcome(resp.json())

        result, receipt = await execute_actor_operation(
            "health", {}, self._probe_apex, callback
        )
        response = dict(result)
        response["akos_receipt"] = receipt_to_dict(receipt)
        return response



# ── CLI quick-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def _test():
        connector = ActorViolationsConnector()
        health = await connector.health()
        print("APEX Health:", json.dumps(health, indent=2))

    asyncio.run(_test())
