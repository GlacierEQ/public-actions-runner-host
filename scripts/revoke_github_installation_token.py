#!/usr/bin/env python3
"""Revoke one short-lived GitHub App installation token without exposing it."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.github.com/installation/token"
API_VERSION = "2026-03-10"
TOKEN_ENV = "GITHUB_INSTALLATION_TOKEN"


class RevocationError(RuntimeError):
    """Fail-closed revocation error without credential material."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RevocationError("github_token_revoke_redirect_rejected")


_OPENER = urllib.request.build_opener(_RejectRedirects())


def main() -> int:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise RevocationError("github_installation_token_missing")
    if "\n" in token or "\r" in token:
        raise RevocationError("github_installation_token_invalid")
    print(f"::add-mask::{token}")
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
        },
        method="DELETE",
    )
    try:
        with _OPENER.open(request, timeout=20) as response:
            status = response.status
            response.read(1)
    except RevocationError:
        raise
    except urllib.error.HTTPError as error:
        raise RevocationError(f"github_token_revoke_http_{error.code}") from error
    except urllib.error.URLError as error:
        raise RevocationError("github_token_revoke_transport_failed") from error
    finally:
        token = ""
    if status != 204:
        raise RevocationError(f"github_token_revoke_unexpected_status_{status}")
    print("GitHub installation token revoked.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RevocationError as error:
        print(f"GITHUB_TOKEN_REVOKE_BLOCK: {error}", file=sys.stderr)
        raise SystemExit(1) from error
