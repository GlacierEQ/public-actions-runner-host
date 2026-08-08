#!/usr/bin/env python3
"""Exchange GitHub Actions OIDC for a narrow Keymaster-minted GitHub App token."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUDIENCE = "apex-keymaster-public-runner"
BROKER_URL = (
    "https://dyhprklicgewmrimecey.supabase.co/functions/v1/"
    "apex-github-oidc-broker"
)
MAX_RESPONSE_BYTES = 64 * 1024
ALLOWED_PERMISSIONS = {"contents", "actions"}
ALLOWED_LEVELS = {"read", "write"}


class TokenBrokerError(RuntimeError):
    """Fail-closed broker error without credential material."""


def _request_json(request: urllib.request.Request) -> dict[str, object]:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise TokenBrokerError(f"broker_http_{error.code}") from error
    except urllib.error.URLError as error:
        raise TokenBrokerError("broker_transport_failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TokenBrokerError("broker_response_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TokenBrokerError("broker_invalid_json") from error
    if not isinstance(payload, dict):
        raise TokenBrokerError("broker_invalid_payload")
    return payload


def _oidc_token() -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not request_url or not request_token:
        raise TokenBrokerError("github_oidc_environment_unavailable")
    separator = "&" if "?" in request_url else "?"
    url = f"{request_url}{separator}{urllib.parse.urlencode({'audience': AUDIENCE})}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {request_token}"},
        method="GET",
    )
    payload = _request_json(request)
    value = payload.get("value")
    if not isinstance(value, str) or value.count(".") != 2:
        raise TokenBrokerError("github_oidc_token_invalid")
    return value


def _permissions(values: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for value in values:
        name, separator, level = value.partition("=")
        if not separator or name not in ALLOWED_PERMISSIONS or level not in ALLOWED_LEVELS:
            raise TokenBrokerError("invalid_permission")
        output[name] = level
    return output or {"contents": "read"}


def _request_id(repository: str, operation: str) -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    safe_repo = repository.replace("/", "-")
    return f"gha-{run_id}-{attempt}-{safe_repo}-{operation}"[:256]


def _write_outputs(token: str, expires_at: str, receipt_id: object) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        raise TokenBrokerError("github_output_unavailable")
    if "\n" in token or "\r" in token:
        raise TokenBrokerError("token_contains_control_character")
    print(f"::add-mask::{token}")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"token={token}\n")
        handle.write(f"expires_at={expires_at}\n")
        handle.write(f"receipt_id={receipt_id}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--permission", action="append", default=[])
    parser.add_argument("--operation", required=True)
    args = parser.parse_args()

    repository = args.repository.strip()
    operation = args.operation.strip()
    if repository.count("/") != 1 or not operation or len(operation) > 256:
        raise TokenBrokerError("invalid_request")

    oidc = _oidc_token()
    body = json.dumps(
        {
            "repository": repository,
            "permissions": _permissions(args.permission),
            "operation": operation,
            "request_id": _request_id(repository, operation),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        BROKER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {oidc}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        payload = _request_json(request)
    finally:
        oidc = ""

    token = payload.get("token")
    expires_at = payload.get("expires_at")
    receipt_id = payload.get("receipt_id")
    if payload.get("ok") is not True or not isinstance(token, str) or not token:
        raise TokenBrokerError("token_mint_failed")
    if not isinstance(expires_at, str) or not expires_at or receipt_id is None:
        raise TokenBrokerError("token_mint_receipt_invalid")
    _write_outputs(token, expires_at, receipt_id)
    token = ""
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TokenBrokerError as error:
        print(f"KEYMASTER_OIDC_BLOCK: {error}", file=sys.stderr)
        raise SystemExit(1) from error
