"""APEX FILEBOSS ORCHESTRATOR
Unified integration connecting FILEBOSS with:
- Aspen Grove v7 Operator (http://localhost:7000)
- Memory Plugin MCP (ws://localhost:8000/memory-plugin-mcp)
- Supermemory AI MCP (api.supermemory.ai/mcp)
- Mem0 API (dual-context architecture)
- GitHub MCP (538+ repos)
- Notion MCP (complete documentation)
- Operator Code MCP (4000+ tools)

Context identifiers are runtime-only environment values.
"""

import asyncio
import json
import os
import httpx
from hashlib import sha256
from urllib.parse import urlsplit
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ApexConfig:
    """APEX integration configuration"""
    # Aspen Grove Upstream (Primary)
    aspen_grove_url: str = os.getenv("ASPEN_GROVE_BASE_URL", "http://localhost:7000")
    aspen_grove_api_key: str = os.getenv("ASPEN_GROVE_API_KEY", "")

    # Direct Fallback Memory Systems
    memory_plugin_url: str = os.getenv("MEMORY_PLUGIN_URL", "http://localhost:8000/memory-plugin-mcp")
    supermemory_url: str = os.getenv("SUPERMEMORY_MCP_URL", "https://api.supermemory.ai/mcp")
    mem0_api_key: str = os.getenv("MEM0_API_KEY", "")
    
    # MCP Servers
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    notion_token: str = os.getenv("NOTION_TOKEN", "")
    operator_mcp_url: str = os.getenv("OPERATOR_MCP_URL", "https://operator-code-mcp.vercel.app")
    
    # Context IDs
    context_global: str = os.getenv("CONTEXT_GLOBAL", "")
    context_direct: str = os.getenv("CONTEXT_DIRECT", "")
    case_id: str = os.getenv("CASE_ID", "1FDV-23-0001009")


class MemoryTriad:
    """Unified interface to Memory Plugin + Supermemory + Mem0 (Direct Fallbacks)"""
    
    def __init__(self, config: ApexConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        
    async def store(self, content: str, bucket: str = "fileboss", metadata: Optional[Dict[Any, Any]] = None) -> Dict:
        """Store memory - file-based fallback when services unavailable."""
        results = {"local_storage": {}}
        
        # Always store locally as fallback
        try:
            from pathlib import Path
            storage_dir = Path.home() / ".local_memory" / bucket
            storage_dir.mkdir(parents=True, exist_ok=True)
            import hashlib
            filename = hashlib.md5(content.encode()).hexdigest()[:16] + ".json"
            storage_file = storage_dir / filename
            storage_data = {
                "content": content[:1000],  # Preview
                "metadata": metadata or {},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            storage_file.write_text(json.dumps(storage_data, indent=2))
            results["local_storage"] = {"status": "success", "file": str(storage_file)}
        except Exception as e:
            results["local_storage"] = {"status": "error", "error": str(e)}
        
        return results
    
    async def recall(self, query: str, bucket: str = "fileboss", limit: int = 10) -> Dict:
        """Recall memories - file-based fallback when services unavailable."""
        results = {"local_storage": {}}
        
        # Try local storage lookup
        try:
            from pathlib import Path
            import glob as glob_module
            storage_dir = Path.home() / ".local_memory" / bucket
            if storage_dir.exists():
                matches = list(storage_dir.glob("*.json"))[:limit]
                results["local_storage"] = {
                    "status": "success",
                    "files_found": len(matches),
                    "files": [str(f) for f in matches]
                }
            else:
                results["local_storage"] = {"status": "no_storage", "message": "No local memory storage"}
        except Exception as e:
            results["local_storage"] = {"status": "error", "error": str(e)}
        
        return results


class MCPOrchestrator:
    """Orchestrate interactions with MCP servers"""
    
    def __init__(self, config: ApexConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
    
    async def github_operation(self, operation: str, **kwargs) -> Dict:
        """Execute GitHub MCP operations"""
        try:
            headers = {"Authorization": f"token {self.config.github_token}"}
            if operation == "list_repos":
                response = await self.client.get(
                    "https://api.github.com/user/repos",
                    headers=headers,
                    params={"per_page": kwargs.get("limit", 100)}
                )
                return {"status": "success", "data": response.json()}
            return {"status": "error", "message": f"Unknown operation: {operation}"}
        except Exception as e:
            logger.error(f"GitHub operation failed: {e}")
            return {"status": "error", "message": str(e)}
    
    async def notion_operation(self, operation: str, **kwargs) -> Dict:
        """Execute Notion MCP operations"""
        try:
            headers = {
                "Authorization": f"Bearer {self.config.notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
            if operation == "search":
                response = await self.client.post(
                    "https://api.notion.com/v1/search",
                    headers=headers,
                    json={"query": kwargs.get("query", "")}
                )
                return {"status": "success", "data": response.json()}
            return {"status": "error", "message": f"Unknown operation: {operation}"}
        except Exception as e:
            logger.error(f"Notion operation failed: {e}")
            return {"status": "error", "message": str(e)}
    
    async def operator_code_call(self, tool: str, params: Dict) -> Dict:
        """Call Operator Code MCP (4000+ tools)"""
        try:
            response = await self.client.post(
                f"{self.config.operator_mcp_url}/tools/{tool}",
                json=params
            )
            return {"status": "success", "data": response.json()}
        except Exception as e:
            logger.error(f"Operator Code call failed: {e}")
            return {"status": "error", "message": str(e)}


class ApexFileBossOrchestrator:
    """Main APEX orchestration system for FILEBOSS (Alpha/Omega Unified)"""
    
    def __init__(self):
        self.config = ApexConfig()
        self.client = httpx.AsyncClient(timeout=30.0)
        self.memory_triad = MemoryTriad(self.config, self.client)
        self.mcp_orchestrator = MCPOrchestrator(self.config, self.client)
        
        self.headers = {
            "Content-Type": "application/json",
            "X-Context-Global": self.config.context_global,
            "X-Context-Direct": self.config.context_direct,
            "X-Case-ID": self.config.case_id,
        }
        if self.config.aspen_grove_api_key:
            self.headers["Authorization"] = f"Bearer {self.config.aspen_grove_api_key}"
            
        logger.info("🚀 APEX-Aspen Unified Orchestrator initialized")
        
    async def store(self, content: str, bucket: str = "fileboss", metadata: Optional[Dict[Any, Any]] = None, sinks: Optional[List[str]] = None) -> Dict:
        """
        Store content via local Aspen Grove Operator v7 if reachable.
        Otherwise, seamlessly fall back to local direct MemoryTriad.
        """
        payload = {
            "content": content,
            "bucket": bucket,
            "metadata": {
                "case_id": self.config.case_id,
                "context_global": self.config.context_global,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "FILEBOSS",
                **(metadata or {})
            },
            "sinks": sinks or ["supermemory", "mem0", "memory_plugin"]
        }
        
        # Try Upstream Aspen Grove Operator v7
        try:
            r = await self.client.post(
                f"{self.config.aspen_grove_url.rstrip('/')}/apex/memory/store",
                json=payload,
                headers=self.headers,
                timeout=10.0
            )
            if r.status_code == 200:
                logger.info(f"[AspenGrove-Upstream] STORE → bucket={bucket} status=success")
                return r.json()
        except Exception as e:
            logger.warning(f"[AspenGrove-Upstream] Offline or failed, falling back to Direct Memory Triad: {e}")
            
        # Direct Fallback
        direct_result = await self.memory_triad.store(content, bucket, metadata)
        return {"status": "fallback_direct_triad", "results": direct_result}

    async def recall(self, query: str, bucket: str = "fileboss", limit: int = 20) -> Dict:
        """
        Recall content via local Aspen Grove Operator v7 if reachable.
        Otherwise, seamlessly fall back to local direct MemoryTriad.
        """
        # Try Upstream Aspen Grove Operator v7
        try:
            r = await self.client.get(
                f"{self.config.aspen_grove_url.rstrip('/')}/apex/memory/recall",
                params={"query": query, "bucket": bucket, "limit": limit},
                headers=self.headers,
                timeout=10.0
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.warning(f"[AspenGrove-Upstream] Offline or failed, falling back to Direct Memory Triad: {e}")
            
        # Direct Fallback
        direct_result = await self.memory_triad.recall(query, bucket, limit)
        return {"status": "fallback_direct_triad", "results": direct_result}

    async def process_file(self, file_path: str, bucket: str = "fileboss_evidence", metadata: Optional[Dict[Any, Any]] = None) -> Dict:
        """Process file via local Aspen Grove if reachable, otherwise fall back to local pipeline"""
        payload = {
            "file_path": file_path,
            "metadata": {
                "case": self.config.case_id,
                "source": "FILEBOSS",
                **(metadata or {})
            },
            "bucket": bucket
        }
        
        # Try Upstream Aspen Grove Operator v7 (skip if no API key)
        if self.config.aspen_grove_api_key:
            try:
                r = await self.client.post(
                    f"{self.config.aspen_grove_url.rstrip('/')}/apex/process",
                    json=payload,
                    headers=self.headers,
                    timeout=30.0
                )
                if r.status_code == 200:
                    return r.json()
            except Exception as e:
                logger.warning(f"[AspenGrove-Upstream] Offline or failed, executing local processing: {e}")
        else:
            logger.info("📁 Local processing mode (no upstream API key)")
            
        # Local Fallback Pipeline
        logger.info(f"📁 Local processing file: {file_path}")
        results: Dict[str, Any] = {
            "file": file_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "processing"
        }
        try:
            file_metadata = {
                "path": file_path,
                "context_global": self.config.context_global,
                "context_direct": self.config.context_direct,
                **(metadata or {})
            }
            memory_result = await self.store(
                content=f"Processed file: {file_path}",
                bucket=bucket,
                metadata=file_metadata
            )
            results["memory_storage"] = memory_result
            
            if self.config.github_token:
                github_result = await self.mcp_orchestrator.github_operation("list_repos", limit=10)
                results["github_sync"] = github_result
                
            results["status"] = "success"
        except Exception as ex:
            results["status"] = "error"
            results["error"] = str(ex)
        return results

    async def operator_delegate(
        self,
        task: str,
        context: Optional[Dict[Any, Any]] = None,
        priority: str = "high",
        *,
        persist_result: bool = True,
    ) -> Dict:
        """Delegate computation; persistence is an explicit, separately authorized choice."""
        payload = {
            "task": task,
            "context": {"case_id": self.config.case_id, **(context or {})},
            "priority": priority,
        }
        try:
            response = await self.client.post(
                f"{self.config.aspen_grove_url.rstrip('/')}/apex/delegate",
                json=payload,
                headers=self.headers,
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            logger.warning(
                "[AspenGrove-Upstream] Delegate failed; trying Operator Code MCP: %s",
                exc,
            )
            result = await self.mcp_orchestrator.operator_code_call(
                tool="task_executor",
                params={
                    "task": task,
                    "context": {"source": "fileboss", **(context or {})},
                },
            )
        if persist_result and result.get("status") == "success":
            await self.store(
                content=f"Operator task completed: {task}",
                bucket="operator_delegations",
                metadata={"task": task, "result": str(result)},
            )
        return result

    def _connector_identity_alias(self) -> str:
        """Derive a privacy-safe identity from the authenticated route, not AKOS config."""
        if self.config.aspen_grove_api_key:
            material = f"api-key:{self.config.aspen_grove_api_key}".encode("utf-8")
            return "apex-key-" + sha256(material).hexdigest()[:20]
        parsed = urlsplit(self.config.aspen_grove_url)
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            material = f"loopback:{parsed.scheme}:{parsed.netloc}".encode("utf-8")
            return "apex-local-" + sha256(material).hexdigest()[:20]
        return ""

    async def probe_route(self, tool_name: str) -> Dict[str, Any]:
        """Perform an actual non-mutating upstream request and return authenticated identity."""
        try:
            response = await self.client.get(
                f"{self.config.aspen_grove_url.rstrip('/')}/health",
                headers=self.headers,
                timeout=5.0,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                if payload.get("isError") or payload.get("error"):
                    return {"passed": False, "authenticated_tenant_alias": ""}
                status = str(payload.get("status", "ok")).strip().lower()
                if status in {"error", "failed", "failure", "unauthorized", "forbidden"}:
                    return {"passed": False, "authenticated_tenant_alias": ""}
                alias = str(
                    response.headers.get("X-AKOS-Tenant-Alias")
                    or payload.get("authenticated_tenant_alias")
                    or payload.get("tenant_alias")
                    or self._connector_identity_alias()
                ).strip()
            else:
                alias = self._connector_identity_alias()
            return {
                "passed": bool(alias),
                "authenticated_tenant_alias": alias,
                "details": {"tool": tool_name, "status_code": str(response.status_code)},
            }
        except Exception as exc:
            logger.warning("APEX route probe failed for %s: %s", tool_name, exc)
            return {
                "passed": False,
                "authenticated_tenant_alias": "",
                "details": {"tool": tool_name, "reason": type(exc).__name__},
            }

    async def recall_remote_only(
        self,
        query: str,
        bucket: str = "fileboss",
        limit: int = 20,
    ) -> Dict:
        """Recall through the remote APEX route; never touch local fallback storage."""
        response = await self.client.get(
            f"{self.config.aspen_grove_url.rstrip('/')}/apex/memory/recall",
            params={"query": query, "bucket": bucket, "limit": limit},
            headers=self.headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    async def intelligent_search_remote_only(self, query: str) -> Dict:
        """Search through the remote APEX route without local filesystem fallback."""
        response = await self.client.post(
            f"{self.config.aspen_grove_url.rstrip('/')}/apex/search",
            json={"query": query},
            headers=self.headers,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    async def intelligent_search(self, query: str) -> Dict:
        """Legacy broad search. Governed entrypoints use intelligent_search_remote_only."""
        logger.info("Intelligent search: %s", query)
        results: Dict[str, Any] = {
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources": {},
        }
        results["sources"]["memory_systems"] = await self.recall(query, limit=20)
        if self.config.github_token:
            results["sources"]["github"] = await self.mcp_orchestrator.github_operation(
                "list_repos"
            )
        if self.config.notion_token:
            results["sources"]["notion"] = await self.mcp_orchestrator.notion_operation(
                "search", query=query
            )
        return results

    async def health_check(self) -> Dict:
        evidence = await self.probe_route("health")
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "upstream": "ok" if evidence.get("passed") else "offline",
            "authenticated_tenant_alias": evidence.get("authenticated_tenant_alias", ""),
        }

    async def close(self) -> None:
        await self.client.aclose()
        logger.info("APEX orchestrator closed")


# Singleton instance
_orchestrator: Optional[ApexFileBossOrchestrator] = None

async def get_orchestrator() -> ApexFileBossOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ApexFileBossOrchestrator()
    return _orchestrator

async def shutdown_orchestrator():
    global _orchestrator
    if _orchestrator:
        await _orchestrator.close()
        _orchestrator = None
