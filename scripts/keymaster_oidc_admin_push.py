#!/usr/bin/env python3
"""Mint the one-run workflow-write token used only for the OIDC cutover patch."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUDIENCE = "apex-keymaster-admin-patch"
BROKER_URL = (
    "https://dyhprklicgewmrimecey.supabase.co/functions/v1/"
    "apex-github-oidc-admin-broker"
)
MAX_BYTES = 64 * 1024


def request_json(request: urllib.request.Request) -> dict[str, object]:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_BYTES + 1)
        detail = "unknown"
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
                detail = parsed["error"][:160]
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        raise RuntimeError(f"http_{error.code}:{detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError("transport_failed") from error
    if len(raw) > MAX_BYTES:
        raise RuntimeError("response_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_payload")
    return payload


def main() -> int:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not url or not bearer or not output:
        raise RuntimeError("oidc_environment_unavailable")
    sep = "&" if "?" in url else "?"
    oidc_request = urllib.request.Request(
        f"{url}{sep}{urllib.parse.urlencode({'audience': AUDIENCE})}",
        headers={"Authorization": f"Bearer {bearer}"},
        method="GET",
    )
    oidc = request_json(oidc_request).get("value")
    if not isinstance(oidc, str) or oidc.count(".") != 2:
        raise RuntimeError("oidc_invalid")
    try:
        broker_request = urllib.request.Request(
            BROKER_URL,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {oidc}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        payload = request_json(broker_request)
    finally:
        oidc = ""
    token = payload.get("token")
    expires_at = payload.get("expires_at")
    receipt_id = payload.get("receipt_id")
    if payload.get("ok") is not True or not isinstance(token, str) or not token:
        raise RuntimeError("admin_token_mint_failed")
    if not isinstance(expires_at, str) or receipt_id is None:
        raise RuntimeError("admin_token_receipt_invalid")
    print(f"::add-mask::{token}")
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"token={token}\n")
        handle.write(f"expires_at={expires_at}\n")
        handle.write(f"receipt_id={receipt_id}\n")
    token = ""
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"KEYMASTER_ADMIN_BLOCK: {error}", file=sys.stderr)
        raise SystemExit(1) from error
