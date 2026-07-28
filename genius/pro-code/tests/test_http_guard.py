from __future__ import annotations

import pytest

from smithery_control_plane.runtime.http_guard import (
    HttpSecurityConfig,
    RequestRejected,
    bearer_authorized,
    encoded_json_size,
    security_headers,
    validate_jsonrpc_payload,
)


def config(**changes):
    base = HttpSecurityConfig()
    return HttpSecurityConfig(**{**base.__dict__, **changes})


def request(**changes):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    payload.update(changes)
    return payload


def test_optional_bearer_auth_is_constant_shape() -> None:
    assert bearer_authorized(None, "") is True
    assert bearer_authorized("Bearer correct", "correct") is True
    assert bearer_authorized("bearer correct", "correct") is True
    assert bearer_authorized("Basic correct", "correct") is False
    assert bearer_authorized("Bearer wrong", "correct") is False


def test_security_headers_are_fail_closed() -> None:
    headers = security_headers()
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Strict-Transport-Security"].startswith("max-age=")


def test_jsonrpc_accepts_single_and_bounded_batch() -> None:
    assert validate_jsonrpc_payload(request(), config())[0]["method"] == "tools/list"
    assert len(validate_jsonrpc_payload([request(id=1), request(id=2)], config())) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"jsonrpc": "1.0", "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "method": "", "params": {}},
        {"jsonrpc": "2.0", "method": "tools/list", "params": []},
        {"jsonrpc": "2.0", "id": True, "method": "tools/list", "params": {}},
        [],
    ],
)
def test_jsonrpc_rejects_malformed_envelopes(payload) -> None:
    with pytest.raises(RequestRejected):
        validate_jsonrpc_payload(payload, config())


def test_jsonrpc_rejects_batch_depth_nodes_and_strings() -> None:
    with pytest.raises(RequestRejected):
        validate_jsonrpc_payload([request(), request()], config(max_batch_size=1))
    with pytest.raises(RequestRejected):
        validate_jsonrpc_payload(
            request(params={"a": {"b": {"c": {"d": {"e": 1}}}}}),
            config(max_input_depth=4),
        )
    with pytest.raises(RequestRejected):
        validate_jsonrpc_payload(
            request(params={"items": list(range(101))}),
            config(max_input_nodes=100),
        )
    with pytest.raises(RequestRejected):
        validate_jsonrpc_payload(
            request(params={"query": "x" * 1025}),
            config(max_string_chars=1024),
        )


def test_environment_limits_are_bounded() -> None:
    loaded = HttpSecurityConfig.from_environment(
        {
            "FILEBOSS_MAX_REQUEST_BYTES": "2048",
            "FILEBOSS_MAX_BATCH_SIZE": "8",
            "FILEBOSS_MAX_INPUT_NODES": "1000",
            "FILEBOSS_MAX_INPUT_DEPTH": "12",
            "FILEBOSS_MAX_STRING_CHARS": "4096",
            "FILEBOSS_MCP_BEARER_TOKEN": " token ",
        }
    )
    assert loaded.max_request_bytes == 2048
    assert loaded.max_batch_size == 8
    assert loaded.bearer_token == "token"
    with pytest.raises(RequestRejected):
        HttpSecurityConfig.from_environment({"FILEBOSS_MAX_BATCH_SIZE": "0"})


def test_encoded_json_size_measures_utf8_bytes() -> None:
    assert encoded_json_size({"value": "é"}) > len('{"value":"é"}')
