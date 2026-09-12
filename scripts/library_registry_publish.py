#!/usr/bin/env python3
"""Atomically publish generated Library-of-Links registry projections at an exact head."""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO = "GlacierEQ/library-of-links"
API = f"https://api.github.com/repos/{REPO}"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
GENERATED = ("registry/index.json", "registry/top_shelf.json")


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GlacierEQ-library-registry-publisher",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"GitHub API {method} {path} failed: HTTP {exc.code}: {body}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--message", default="Rebuild knowledge registry")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_INSTALLATION_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_INSTALLATION_TOKEN is required")
    if not SHA40.fullmatch(args.source_ref):
        raise SystemExit("--source-ref must be an exact 40-character SHA")

    workspace = Path(args.workspace).resolve()
    entries = []
    for relative in GENERATED:
        path = workspace / relative
        if not path.is_file():
            raise SystemExit(f"generated projection missing: {relative}")
        entries.append(
            {
                "path": relative,
                "mode": "100644",
                "type": "blob",
                "content": path.read_text(encoding="utf-8"),
            }
        )

    live_ref = request("GET", "/git/ref/heads/main", token)["object"]["sha"]
    if live_ref != args.source_ref:
        raise SystemExit(
            f"HEAD_MOVED: expected {args.source_ref}, provider reports {live_ref}; refusing publish"
        )

    parent = request("GET", f"/git/commits/{args.source_ref}", token)
    tree = request(
        "POST",
        "/git/trees",
        token,
        {"base_tree": parent["tree"]["sha"], "tree": entries},
    )
    commit = request(
        "POST",
        "/git/commits",
        token,
        {"message": args.message, "tree": tree["sha"], "parents": [args.source_ref]},
    )
    request(
        "PATCH",
        "/git/refs/heads/main",
        token,
        {"sha": commit["sha"], "force": False},
    )
    readback = request("GET", "/git/ref/heads/main", token)["object"]["sha"]
    if readback != commit["sha"]:
        raise SystemExit(f"READBACK_MISMATCH: expected {commit['sha']}, got {readback}")

    print(
        "LIBRARY_REGISTRY_PUBLISH_OK "
        + json.dumps(
            {
                "repository": REPO,
                "source_ref": args.source_ref,
                "published_sha": commit["sha"],
                "paths": list(GENERATED),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
