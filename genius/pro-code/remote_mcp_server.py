#!/usr/bin/env python3
"""
FILEBOSS Remote MCP Server (v0.4.2)

Production-grade Streamable HTTP MCP server for evidence, legal, memory, and
safe local file-organization operations.

Design goals:
- Full v0.4 maximized compliance (12 laws + 7 gates)
- Structured observability with correlation IDs and latency tracking
- Clear, example-rich tool schemas
- Graceful degradation when APEX orchestrator is unavailable
- Safe, dependency-aware local file organization (dry-run by default)

Transport: Streamable HTTP (MCP spec 2025-03-26)
"""

import asyncio
import json
import os
import logging
import time
import uuid

from smithery_control_plane.runtime.connector_gateway import (
    ExecutionOutcome,
    ProbeEvidence,
    compute_outcome,
    default_gateway,
    execute_mcp_tool,
    read_outcome,
    resolve_mcp_tool_spec,
)
from smithery_control_plane.runtime.deployment_bootstrap import configure_deployment_runtime
from smithery_control_plane.runtime.http_guard import (
    HttpSecurityConfig,
    RequestRejected,
    bearer_authorized,
    security_headers,
    validate_jsonrpc_payload,
)
from smithery_control_plane.runtime.connector_policy import (
    CompletionRejected,
    NoApprovedRoute,
    Operation,
    PolicyError,
    receipt_to_dict,
    repair_instruction_to_dict,
    sanitize_audit_metadata,
)

from fastapi import FastAPI, Request, Response
from sse_starlette.sse import EventSourceResponse
import uvicorn

logger = logging.getLogger("fileboss.mcp")

configure_deployment_runtime()

app = FastAPI(title="FILEBOSS Remote MCP", version="2.0.5")
_HTTP_SECURITY = HttpSecurityConfig.from_environment()
_AKOS_READY = False


def _secure_response(response: Response) -> Response:
    for name, value in security_headers().items():
        response.headers.setdefault(name, value)
    return response


def _json_http_response(
    payload: object,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    response = Response(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )
    return _secure_response(response)


@app.middleware("http")
async def enforce_mcp_edge_security(request: Request, call_next):
    if request.url.path == "/mcp":
        if not bearer_authorized(
            request.headers.get("authorization"),
            _HTTP_SECURITY.bearer_token,
        ):
            return _json_http_response(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    declared = int(content_length)
                except ValueError:
                    return _json_http_response(
                        {"error": "invalid_content_length"},
                        status_code=400,
                    )
                if declared < 0 or declared > _HTTP_SECURITY.max_request_bytes:
                    return _json_http_response(
                        {"error": "request_too_large"},
                        status_code=413,
                    )
            body = await request.body()
            if len(body) > _HTTP_SECURITY.max_request_bytes:
                return _json_http_response(
                    {"error": "request_too_large"},
                    status_code=413,
                )
    response = await call_next(request)
    return _secure_response(response)


@app.on_event("startup")
async def validate_akos_runtime_on_startup() -> None:
    """Load the protected trust root before the service may report healthy."""
    global _AKOS_READY
    gateway = default_gateway()
    repair_sink = getattr(gateway.policy, "_repair_sink", None)
    verify_chain = getattr(repair_sink, "verify_chain", None)
    if callable(verify_chain):
        verify_chain(force=True)
    _AKOS_READY = True


# ── Observability Helpers ─────────────────────────────────────────────────────
def _validated_internal_correlation_id(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def log_event(event: str, correlation_id: str, **kwargs):
    log_data = sanitize_audit_metadata(
        {
            "event": event,
            "timestamp": time.time(),
            **kwargs,
        }
    )
    internal_id = _validated_internal_correlation_id(correlation_id)
    log_data["correlation_id"] = (
        internal_id if internal_id is not None else "[REDACTED_IDENTIFIER]"
    )
    logger.info(json.dumps(log_data, sort_keys=True))


def track_latency(start_time: float, correlation_id: str, tool: str):
    duration = time.time() - start_time
    log_event("tool_latency", correlation_id, tool=tool, duration_ms=round(duration * 1000, 2))


# ── MCP Tool Registry ──────────────────────────────────────────────────────────
TOOLS = {
    "fileboss_search": {
        "description": (
            "Search files, legal documents, and case evidence using APEX tri-source engine "
            "(Memory + GitHub + Exa). Returns ranked results with source attribution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Example: 'HRS 571-46 best interests'",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["memory", "github", "exa", "legal"]},
                    "description": "Which sources to query (default: all)",
                },
                "limit": {"type": "integer", "default": 10, "description": "Maximum results to return"},
            },
            "required": ["query"],
        },
    },
    "fileboss_case_evidence": {
        "description": (
            "Access case 1FDV-23-0001009 evidence database. Supports retrieve, add, tag, and list operations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "add", "tag", "list"],
                    "description": "Operation to perform",
                },
                "evidence_id": {"type": "string", "description": "Evidence identifier (required for get/tag)"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to apply"},
                "content": {"type": "string", "description": "Content to store (for add action)"},
            },
            "required": ["action"],
        },
    },
    "fileboss_legal_search": {
        "description": (
            "Search Hawaii case law, federal § 1983 precedents, HRPC violations via CourtListener + Lexis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Legal search query. Example: 'judicial misconduct Hawaii'",
                },
                "jurisdiction": {
                    "type": "string",
                    "enum": ["hawaii", "ninth_circuit", "federal", "all"],
                    "default": "hawaii",
                },
                "statutes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific statutes to filter on. Example: ['HRS 571-46', '42 USC 1983']",
                },
            },
            "required": ["query"],
        },
    },
    "fileboss_memory_store": {
        "description": "Store information to APEX Memory Triad (Mem0 + Supermemory + Memory Plugin).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to store"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "apex"],
                    "default": "normal",
                },
            },
            "required": ["content"],
        },
    },
    "fileboss_memory_recall": {
        "description": "Recall from APEX Memory Triad with semantic search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic search query"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    "fileboss_dropbox_sync": {
        "description": "Sync or retrieve files from case 1FDV-23-0001009 Dropbox evidence folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "sync", "get", "upload"],
                    "description": "Operation to perform",
                },
                "path": {"type": "string", "default": "/Case-1FDV-23-0001009"},
                "filename": {"type": "string", "description": "Filename for get/upload"},
            },
            "required": ["action"],
        },
    },
    "fileboss_motion_draft": {
        "description": (
            "Draft legal motions using APEX engine. Supports motion_to_recuse, motion_to_compel, "
            "1983_complaint, hrpc_complaint, motion_in_limine, appeal_brief."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "motion_type": {
                    "type": "string",
                    "enum": [
                        "motion_to_recuse",
                        "motion_to_compel",
                        "1983_complaint",
                        "hrpc_complaint",
                        "motion_in_limine",
                        "appeal_brief",
                    ],
                },
                "facts": {"type": "string", "description": "Key facts to incorporate"},
                "statutes": {"type": "array", "items": {"type": "string"}},
                "respondent": {"type": "string"},
            },
            "required": ["motion_type", "facts"],
        },
    },
    # ── Local filesystem organization (safe, dependency-aware) ─────────────────────────────────
    "fileboss_local_analyze": {
        "description": (
            "READ-ONLY. Recursively analyze a local folder (e.g. Documents) and return a "
            "non-destructive organization plan. Automatically skips program directories, "
            "source-code project roots, virtual environments, executables, libraries, and "
            "manifest/config files that other files depend on. Never modifies disk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the folder to analyze. Example: '/Users/casey/Documents'"},
                "hashes": {"type": "boolean", "default": False, "description": "Compute SHA-256 for duplicate detection (slower)"},
            },
            "required": ["path"],
        },
    },
    "fileboss_local_index": {
        "description": (
            "Build/refresh a searchable SQLite index of all user documents under a folder, "
            "with content hashing for duplicate detection. Read-only scan; writes only the "
            "index file. Same program/dependency protections as analyze."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to index"},
                "hashes": {"type": "boolean", "default": True, "description": "Compute SHA-256 hashes (recommended)"},
            },
            "required": ["path"],
        },
    },
    "fileboss_local_organize": {
        "description": (
            "Organize user documents into category folders. DRY-RUN BY DEFAULT — returns a plan "
            "and a confirm_token. To apply, call again with execute=true and the matching "
            "confirm_token. Never deletes; every move is logged to an undo manifest. Skips all "
            "programs, project roots, dependencies, executables, and config files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to organize"},
                "execute": {"type": "boolean", "default": False, "description": "Set true to apply the plan (requires confirm_token)"},
                "confirm_token": {"type": "string", "description": "Token returned by the dry-run; required to execute"},
            },
            "required": ["path"],
        },
    },
    "fileboss_local_undo": {
        "description": "Reverse a previous organize run using its undo manifest file. Restores every moved file to its original location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The organized root folder (for context)"},
                "manifest_path": {"type": "string", "description": "Absolute path to the .fileboss_undo_*.json manifest"},
            },
            "required": ["path", "manifest_path"],
        },
    },
}

LOCAL_TOOLS = {
    "fileboss_local_analyze",
    "fileboss_local_index",
    "fileboss_local_organize",
    "fileboss_local_undo",
}


# ── MCP Protocol Handlers ──────────────────────────────────────────────────────
async def handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": "2025-03-26",
        "serverInfo": {"name": "fileboss-apex", "version": "2.0.5"},
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False},
            "logging": {},
        },
    }


async def handle_tools_list(params: dict) -> dict:
    return {
        "tools": [
            {"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]}
            for name, spec in TOOLS.items()
        ]
    }


async def _run_local_tool(name: str, args: dict, correlation_id: str) -> dict:
    """Route local filesystem tools to the self-contained organizer engine.

    Runs in a worker thread because the engine performs blocking disk I/O.
    """
    from genius.shared.integrations.local_organizer import run_local_tool

    result = await asyncio.to_thread(run_local_tool, name, args)
    log_event("local_tool_done", correlation_id, tool=name)
    return result


async def _probe_tool_route(name: str, args: dict) -> ProbeEvidence:
    """Contact the selected route and bind authorization to its returned identity."""
    if name in LOCAL_TOOLS:
        return ProbeEvidence(
            False,
            "",
            {"reason": "local_device_identity_and_cloud_receipt_not_proven"},
        )

    from genius.shared.integrations.apex_orchestrator import ApexFileBossOrchestrator

    apex = ApexFileBossOrchestrator()
    try:
        raw = await apex.probe_route(name)
        return ProbeEvidence(
            bool(raw.get("passed")),
            str(raw.get("authenticated_tenant_alias", "")),
            {
                str(key): str(value)
                for key, value in dict(raw.get("details", {})).items()
            },
        )
    finally:
        await apex.close()


async def _execute_tool_unchecked(
    name: str,
    args: dict,
    correlation_id: str,
) -> dict:
    """Execute only after the AKOS gateway authorizes the operation."""
    if name in LOCAL_TOOLS:
        return await asyncio.wait_for(
            _run_local_tool(name, args, correlation_id),
            timeout=600.0,
        )

    from genius.shared.integrations.apex_orchestrator import ApexFileBossOrchestrator

    apex = ApexFileBossOrchestrator()
    try:
        if name in {"fileboss_search", "fileboss_legal_search"}:
            return await asyncio.wait_for(
                apex.intelligent_search_remote_only(str(args.get("query", ""))), timeout=30.0
            )
        if name == "fileboss_memory_recall":
            return await asyncio.wait_for(
                apex.recall_remote_only(
                    str(args.get("query", "")),
                    limit=int(args.get("limit", 5)),
                ),
                timeout=30.0,
            )
        if name == "fileboss_motion_draft":
            return await asyncio.wait_for(
                apex.operator_delegate(
                    task=(
                        f"Draft {args.get('motion_type', 'motion')} using only the supplied facts. "
                        f"Facts: {args.get('facts', '')}"
                    ),
                    context={"statutes": args.get("statutes", [])},
                    priority="high",
                    persist_result=False,
                ),
                timeout=30.0,
            )
        if name == "fileboss_memory_store":
            return await asyncio.wait_for(
                apex.store(
                    str(args.get("content", "")),
                    metadata={
                        "tags": args.get("tags", []),
                        "priority": args.get("priority", "normal"),
                    },
                ),
                timeout=30.0,
            )
        raise PolicyError(f"No executable APEX route for tool: {name}")
    finally:
        await apex.close()


async def handle_tools_call(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments", {})
    if not isinstance(args, dict):
        return {
            "content": [{"type": "text", "text": "Tool arguments must be an object"}],
            "isError": True,
        }
    correlation_id = str(uuid.uuid4())
    start_time = time.time()

    log_event(
        "tool_call_started",
        correlation_id,
        tool=name,
        argument_keys=sorted(str(key) for key in args),
    )

    if name not in TOOLS:
        log_event("tool_call_error", correlation_id, tool=name, error="unknown_tool")
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }

    try:
        spec = resolve_mcp_tool_spec(name, args)

        async def probe():
            return await _probe_tool_route(name, args)

        async def callback():
            raw_result = await _execute_tool_unchecked(name, args, correlation_id)
            if spec.operation in {Operation.READ, Operation.LOCAL_READ}:
                validated = read_outcome(raw_result)
            elif spec.operation is Operation.COMPUTE:
                validated = compute_outcome(raw_result)
            else:
                raise PolicyError(
                    "Write execution lacks a verified postflight proof contract"
                )
            return ExecutionOutcome(
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(validated.result, indent=2),
                        }
                    ]
                },
                completion_proofs=validated.completion_proofs,
                artifact_hashes=validated.artifact_hashes,
            )

        result, receipt = await execute_mcp_tool(name, args, probe, callback)
        response = dict(result)
        response["akos_receipt"] = receipt_to_dict(receipt)
        track_latency(start_time, correlation_id, name)
        log_event(
            "tool_call_success",
            correlation_id,
            tool=name,
            receipt_id=receipt.receipt_id,
        )
        return response

    except NoApprovedRoute as exc:
        track_latency(start_time, correlation_id, name)
        details = {
            "error": "AKOS authorization rejected",
            "failed_gates": dict(exc.failures),
            "repair": repair_instruction_to_dict(exc.repair_instruction),
        }
        log_event(
            "tool_call_blocked",
            correlation_id,
            tool=name,
            failed_gates=dict(exc.failures),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(details, indent=2)}],
            "isError": True,
        }
    except CompletionRejected as exc:
        track_latency(start_time, correlation_id, name)
        details = {
            "error": "AKOS completion rejected",
            "receipt": receipt_to_dict(exc.receipt),
            "repair": repair_instruction_to_dict(exc.repair_instruction),
        }
        log_event(
            "tool_call_completion_rejected",
            correlation_id,
            tool=name,
            failed_gates=list(exc.receipt.failed_gates),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(details, indent=2)}],
            "isError": True,
        }
    except asyncio.TimeoutError:
        track_latency(start_time, correlation_id, name)
        log_event("tool_call_timeout", correlation_id, tool=name)
        return {
            "content": [{"type": "text", "text": "Tool execution timed out"}],
            "isError": True,
        }
    except PolicyError as exc:
        track_latency(start_time, correlation_id, name)
        safe_error = sanitize_audit_metadata(str(exc))
        log_event("tool_call_policy_error", correlation_id, tool=name, error=safe_error)
        details = {"error": str(safe_error)}
        receipt = getattr(exc, "akos_rejected_receipt", None)
        repair = getattr(exc, "akos_repair_instruction", None)
        persistence_error = getattr(exc, "akos_repair_persistence_error", None)
        if receipt is not None:
            details["receipt"] = receipt_to_dict(receipt)
        if repair is not None:
            details["repair"] = repair_instruction_to_dict(repair)
        if persistence_error is not None:
            details["repair_persistence_error"] = persistence_error
        return {
            "content": [{"type": "text", "text": json.dumps(details, indent=2)}],
            "isError": True,
        }
    except Exception as exc:
        track_latency(start_time, correlation_id, name)
        safe_error = sanitize_audit_metadata(str(exc))
        logger.error("Tool handler failed for %s (%s)", name, type(exc).__name__)
        log_event("tool_call_error", correlation_id, tool=name, error=safe_error)
        receipt = getattr(exc, "akos_rejected_receipt", None)
        repair = getattr(exc, "akos_repair_instruction", None)
        details = {"error": str(safe_error)}
        if receipt is not None:
            details["receipt"] = receipt_to_dict(receipt)
        if repair is not None:
            details["repair"] = repair_instruction_to_dict(repair)
        return {
            "content": [{"type": "text", "text": json.dumps(details, indent=2)}],
            "isError": True,
        }


HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


# ── Streamable HTTP Endpoint (MCP 2025-03-26) ─────────────────────────────────
@app.post("/mcp")
async def mcp_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _json_http_response(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )

    try:
        requests = validate_jsonrpc_payload(body, _HTTP_SECURITY)
    except RequestRejected as exc:
        return _json_http_response(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": str(exc)},
            },
            status_code=400,
        )

    if isinstance(body, list):
        responses = [await _process_jsonrpc(req, validated=True) for req in requests]
        return _json_http_response(responses)

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return EventSourceResponse(_sse_generator(requests[0]))

    result = await _process_jsonrpc(requests[0], validated=True)
    return _json_http_response(result)


@app.get("/mcp")
async def mcp_get(request: Request):
    return EventSourceResponse(_heartbeat())


async def _process_jsonrpc(req: dict, *, validated: bool = False) -> dict:
    if not validated:
        try:
            req = validate_jsonrpc_payload(req, _HTTP_SECURITY)[0]
        except RequestRejected as exc:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": str(exc)},
            }
    method = req.get("method", "")
    params = dict(req.get("params", {}))
    req_id = req.get("id")

    handler = HANDLERS.get(method)
    if not handler:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": "Method not found"},
        }

    try:
        result = await handler(params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as exc:
        safe_error = sanitize_audit_metadata(str(exc))
        logger.error("JSON-RPC handler failed for %s (%s)", method, type(exc).__name__)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": str(safe_error)},
        }


async def _sse_generator(req: dict):
    result = await _process_jsonrpc(req)
    yield {"data": json.dumps(result)}


async def _heartbeat():
    while True:
        yield {"event": "ping", "data": ""}
        await asyncio.sleep(30)


# ── Health + Discovery ─────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "FILEBOSS Remote MCP",
        "version": "2.0.5",
        "mcp_endpoint": "/mcp",
        "transport": "Streamable HTTP (MCP 2025-03-26)",
        "status": "ready" if _AKOS_READY else "not_ready",
    }


@app.get("/live")
async def live():
    return {"status": "ok", "version": "2.0.5"}


@app.get("/health")
async def health():
    if not _AKOS_READY:
        return _json_http_response(
            {"status": "error", "akos": "not_ready"},
            status_code=503,
        )
    return {"status": "ok", "akos": "ready", "tools": len(TOOLS), "version": "2.0.5"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
