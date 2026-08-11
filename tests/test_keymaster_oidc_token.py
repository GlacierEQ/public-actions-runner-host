from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "keymaster_oidc_token.py"

spec = importlib.util.spec_from_file_location("keymaster_oidc_token", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _http_error(url: str, body: bytes, code: int = 400) -> HTTPError:
    return HTTPError(url, code, "bad request", {}, io.BytesIO(body))


def test_safe_broker_error_exposes_only_strict_error_code() -> None:
    request = Request(module.BROKER_URL)
    error = _http_error(
        module.BROKER_URL,
        b'{"error":"repository_not_allowlisted"}',
    )

    assert (
        module._safe_http_error(request, error)
        == "broker_http_400:repository_not_allowlisted"
    )


def test_safe_broker_error_suppresses_arbitrary_or_secret_like_text() -> None:
    request = Request(module.BROKER_URL)

    for body in (
        b'{"error":"token ghp_secret_value"}',
        b'{"error":"../../../secret"}',
        b'{"error":"UPPERCASE_DETAIL"}',
        b'{"error":"contains-hyphen"}',
        b'{"message":"repository_not_allowlisted"}',
    ):
        error = _http_error(module.BROKER_URL, body)
        assert module._safe_http_error(request, error) == "broker_http_400"


def test_safe_broker_error_suppresses_non_broker_response() -> None:
    oidc_url = "https://token.actions.githubusercontent.com/example"
    request = Request(oidc_url)
    error = _http_error(oidc_url, b'{"error":"repository_not_allowlisted"}')

    assert module._safe_http_error(request, error) == "broker_http_400"


def test_safe_broker_error_suppresses_invalid_or_oversized_payloads() -> None:
    request = Request(module.BROKER_URL)
    cases = (
        b"not-json",
        b"[]",
        b'{"error":123}',
        b"x" * (module.MAX_BROKER_ERROR_BYTES + 1),
    )

    for body in cases:
        error = _http_error(module.BROKER_URL, body)
        assert module._safe_http_error(request, error) == "broker_http_400"
