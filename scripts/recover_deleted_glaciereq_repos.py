#!/usr/bin/env python3
"""Restore-adjacent activation for the Aug 2026 GlacierEQ repository casualty set.

This script intentionally DOES NOT create replacement repositories. GitHub's supported
personal-account deleted-repository restore path is the GitHub UI. Recreating a missing
namespace before restoring risks losing the original repository history as the primary
recovery path.

Once GitHub restores an original repository, this script:
  * unarchives it;
  * removes the historical Z-BACKUP- prefix when the clean target is free;
  * if the clean target already exists, preserves both histories and renames the restored
    repository to <target>-recovered-full (or a deterministic numbered variant);
  * emits a machine-readable recovery receipt.

No repository is deleted, force-pushed, merged, or overwritten by this script.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any

OWNER = "GlacierEQ"
API = "https://api.github.com"
API_VERSION = "2026-03-10"

CASUALTIES = [
    "Z-BACKUP-APEX-CASE-EVIDENCE-MATRIX",
    "Z-BACKUP-APEX-NEXUS-SUPREME",
    "Z-BACKUP-APEX-OMNIBUS-SUPREME",
    "Z-BACKUP-APEX-PPLX-SUPERTHREAD",
    "Z-BACKUP-APEX-SOVEREIGN-SENTRY",
    "Z-BACKUP-GodMind-Apex-System",
    "Z-BACKUP-MASTERMIND-PRIMORDIAL-PHOENIX",
    "Z-BACKUP-MOTION-LIBRARY-APEX-ORCHESTRATOR",
    "Z-BACKUP-Mastermind-Architecture-Skeleton",
    "Z-BACKUP-Opryxx_Apex",
    "Z-BACKUP-ULTIMATE-REPAIR-APEX",
    "Z-BACKUP-ai-chat-history-mastermind",
    "Z-BACKUP-apex-cli",
    "Z-BACKUP-apex-cognitive-swarm",
    "Z-BACKUP-apex-commander",
    "Z-BACKUP-apex-connector-registry",
    "Z-BACKUP-apex-core",
    "Z-BACKUP-apex-coupler-pipelines",
    "Z-BACKUP-apex-fs-commander",
    "Z-BACKUP-apex-infinity-stones",
    "Z-BACKUP-apex-ish",
    "Z-BACKUP-apex-legal-dashboard",
    "Z-BACKUP-apex-motherduck-engine",
    "Z-BACKUP-apex-obsidian-vault",
    "Z-BACKUP-apex-orchestrator",
    "Z-BACKUP-apex-pplx-cli",
    "Z-BACKUP-apex-taskade-connector",
    "Z-BACKUP-mastermind-core",
    "Z-BACKUP-mastermind-sovereign-edition",
    "Z-BACKUP-APEX-MEMORY-OMNIBUS",
    "Z-BACKUP-apex-memory-agent",
    "Z-BACKUP-apex-memory-orchestrator",
    "Z-BACKUP-aspen-grove-apex-fusion",
    "Z-BACKUP-aspen-grove-integration-project",
    "Z-BACKUP-aspen-grove-operator-v7",
    "Z-BACKUP-Federal-Forensic-Framework",
    "Z-BACKUP-android-forensics",
    "Z-BACKUP-forensic_transcriber",
    "Z-BACKUP-mba-desktop-forensics",
    "OmniRoute",
    "Computer_User",
    "HydraPersonalDefenseSource",
    "grok-build",
]


@dataclass
class Result:
    source: str
    desired: str
    state: str
    final_name: str | None = None
    source_archived_before: bool | None = None
    target_collision: bool = False
    detail: str | None = None


def token() -> str:
    value = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or ""
    if not value:
        raise SystemExit("No GH_PAT or GITHUB_TOKEN available")
    return value


def api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any, dict[str, str]]:
    url = f"{API}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token()}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "GlacierEQ-repository-recovery",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            payload = json.loads(raw) if raw else None
            return resp.status, payload, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = raw.decode("utf-8", "replace")
        return exc.code, payload, dict(exc.headers)


def repo(name: str) -> tuple[int, Any]:
    status, payload, _ = api("GET", f"/repos/{OWNER}/{urllib.parse.quote(name, safe='')}")
    return status, payload


def patch_repo(name: str, **changes: Any) -> tuple[int, Any]:
    status, payload, _ = api(
        "PATCH",
        f"/repos/{OWNER}/{urllib.parse.quote(name, safe='')}",
        changes,
    )
    return status, payload


def desired_name(source: str) -> str:
    prefix = "Z-BACKUP-"
    return source[len(prefix):] if source.startswith(prefix) else source


def available_recovery_name(base: str) -> str:
    candidate = f"{base}-recovered-full"
    status, _ = repo(candidate)
    if status == 404:
        return candidate
    for idx in range(2, 100):
        candidate = f"{base}-recovered-full-{idx}"
        status, _ = repo(candidate)
        if status == 404:
            return candidate
    raise RuntimeError(f"No recovery namespace available for {base}")


def activate(source: str) -> Result:
    desired = desired_name(source)
    status, current = repo(source)
    if status == 404:
        return Result(source, desired, "RESTORE_REQUIRED", detail="Original repository not currently resolvable")
    if status != 200:
        return Result(source, desired, "LOOKUP_FAILED", detail=f"GET source returned HTTP {status}: {current}")

    archived_before = bool(current.get("archived"))

    # Non-Z casualties retain their exact restored name; just reactivate.
    if desired == source:
        if archived_before:
            pstatus, payload = patch_repo(source, archived=False)
            if pstatus != 200:
                return Result(source, desired, "UNARCHIVE_FAILED", source_archived_before=True, detail=f"HTTP {pstatus}: {payload}")
        return Result(source, desired, "ACTIVE", final_name=source, source_archived_before=archived_before)

    target_status, target = repo(desired)
    if target_status == 404:
        pstatus, payload = patch_repo(source, name=desired, archived=False)
        if pstatus == 200:
            return Result(source, desired, "RENAMED_ACTIVE", final_name=payload.get("name", desired), source_archived_before=archived_before)
        return Result(source, desired, "RENAME_FAILED", source_archived_before=archived_before, detail=f"HTTP {pstatus}: {payload}")

    if target_status != 200:
        return Result(source, desired, "TARGET_LOOKUP_FAILED", source_archived_before=archived_before, detail=f"HTTP {target_status}: {target}")

    # Preserve both histories. Remove the Z-BACKUP prefix without overwriting an active target.
    recovered = available_recovery_name(desired)
    pstatus, payload = patch_repo(source, name=recovered, archived=False)
    if pstatus == 200:
        return Result(
            source,
            desired,
            "RENAMED_COLLISION_PRESERVED",
            final_name=payload.get("name", recovered),
            source_archived_before=archived_before,
            target_collision=True,
            detail=f"Existing {OWNER}/{desired} preserved; restored history activated separately for capability reconciliation",
        )
    return Result(
        source,
        desired,
        "COLLISION_RENAME_FAILED",
        source_archived_before=archived_before,
        target_collision=True,
        detail=f"HTTP {pstatus}: {payload}",
    )


def main() -> int:
    results: list[Result] = []
    for name in CASUALTIES:
        result = activate(name)
        results.append(result)
        print(json.dumps(asdict(result), sort_keys=True))
        time.sleep(0.08)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.state] = counts.get(r.state, 0) + 1

    receipt = {
        "schema": "glaciereq.repository-recovery.v1",
        "owner": OWNER,
        "casualty_count": len(CASUALTIES),
        "counts": counts,
        "results": [asdict(r) for r in results],
    }
    out = os.environ.get("RECOVERY_RECEIPT", "repo-recovery-receipt.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"receipt": out, "counts": counts}, sort_keys=True))

    hard_fail = any(
        r.state in {
            "LOOKUP_FAILED",
            "UNARCHIVE_FAILED",
            "RENAME_FAILED",
            "TARGET_LOOKUP_FAILED",
            "COLLISION_RENAME_FAILED",
        }
        for r in results
    )
    return 2 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
