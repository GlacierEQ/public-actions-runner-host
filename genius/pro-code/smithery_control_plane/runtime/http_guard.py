"""Pure-stdlib HTTP and JSON-RPC guards for the FILEBOSS MCP edge."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import os
from typing import Any, Mapping


class RequestRejected(ValueError):
    """Raised when an inbound request violates an edge limit."""


@dataclass(frozen=True)
class HttpSecurityConfig:
    max_request_bytes: int = 1_048_576
    max_batch_size: int = 16
    max_input_nodes: int = 5_000
    max_input_depth: int = 20
    max_string_chars: int = 131_072
    bearer_token: str = ""

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "HttpSecurityConfig":
        env = dict(os.environ if environment is None else environment)

        def positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
            raw = env.get(name, str(default))
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise RequestRejected(f"{name} must be an integer") from exc
            if value < minimum or value > maximum:
                raise RequestRejected(f"{name} must be between {minimum} and {maximum}")
            return value

        return cls(
            max_request_bytes=positive_int(
                "FILEBOSS_MAX_REQUEST_BYTES",
                cls.max_request_bytes,
                minimum=1_024,
                maximum=8_388_608,
            ),
            max_batch_size=positive_int(
                "FILEBOSS_MAX_BATCH_SIZE",
                cls.max_batch_size,
                minimum=1,
                maximum=100,
            ),
            max_input_nodes=positive_int(
                "FILEBOSS_MAX_INPUT_NODES",
                cls.max_input_nodes,
                minimum=100,
                maximum=100_000,
            ),
            max_input_depth=positive_int(
                "FILEBOSS_MAX_INPUT_DEPTH",
                cls.max_input_depth,
                minimum=4,
                maximum=64,
            ),
            max_string_chars=positive_int(
                "FILEBOSS_MAX_STRING_CHARS",
                cls.max_string_chars,
                minimum=1_024,
                maximum=1_048_576,
            ),
            bearer_token=env.get("FILEBOSS_MCP_BEARER_TOKEN", "").strip(),
        )


def bearer_authorized(header: str | None, expected_token: str) -> bool:
    """Constant-time optional bearer check; empty configuration leaves gateway auth in charge."""
    if not expected_token:
        return True
    if not isinstance(header, str):
        return False
    scheme, separator, supplied = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(supplied, expected_token)


def security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Referrer-Policy": "no-referrer",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _validate_tree(value: Any, config: HttpSecurityConfig) -> None:
    stack: list[tuple[str, Any, int]] = [("$", value, 0)]
    nodes = 0
    while stack:
        path, current, depth = stack.pop()
        nodes += 1
        if nodes > config.max_input_nodes:
            raise RequestRejected("JSON-RPC input exceeds node limit")
        if depth > config.max_input_depth:
            raise RequestRejected("JSON-RPC input exceeds depth limit")
        if isinstance(current, str):
            if len(current) > config.max_string_chars:
                raise RequestRejected(f"String input exceeds limit at {path}")
        elif isinstance(current, Mapping):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise RequestRejected(f"Object key is not a string at {path}")
                if len(key) > 256:
                    raise RequestRejected(f"Object key exceeds limit at {path}")
                stack.append((f"{path}.{key}", child, depth + 1))
        elif isinstance(current, (list, tuple)):
            for index, child in enumerate(current):
                stack.append((f"{path}[{index}]", child, depth + 1))
        elif current is None or isinstance(current, (bool, int, float)):
            continue
        else:
            raise RequestRejected(f"Unsupported JSON value at {path}")


def validate_jsonrpc_request(request: Any, config: HttpSecurityConfig) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise RequestRejected("JSON-RPC request must be an object")
    normalized = dict(request)
    if normalized.get("jsonrpc") != "2.0":
        raise RequestRejected("JSON-RPC version must be 2.0")
    method = normalized.get("method")
    if not isinstance(method, str) or not method or len(method) > 128:
        raise RequestRejected("JSON-RPC method is invalid")
    params = normalized.get("params", {})
    if not isinstance(params, Mapping):
        raise RequestRejected("JSON-RPC params must be an object")
    request_id = normalized.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None))):
        raise RequestRejected("JSON-RPC id is invalid")
    _validate_tree(params, config)
    return normalized


def validate_jsonrpc_payload(payload: Any, config: HttpSecurityConfig) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if not payload:
            raise RequestRejected("JSON-RPC batch must not be empty")
        if len(payload) > config.max_batch_size:
            raise RequestRejected("JSON-RPC batch exceeds limit")
        return [validate_jsonrpc_request(item, config) for item in payload]
    return [validate_jsonrpc_request(payload, config)]


def encoded_json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
