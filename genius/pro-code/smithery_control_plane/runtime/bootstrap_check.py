"""Fail deployment when mandatory AKOS runtime values are absent."""

from __future__ import annotations

import os
import sys

REQUIRED = (
    "AKOS_POLICY_SHA256",
    "AKOS_ATTESTATION_HMAC_KEY",
    "AKOS_TENANT_ALIAS",
)


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print("AKOS runtime configuration missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    print("AKOS runtime configuration present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
