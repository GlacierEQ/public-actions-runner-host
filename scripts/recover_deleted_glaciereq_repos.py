#!/usr/bin/env python3
"""Full-history recovery activator for the Aug 2026 GlacierEQ repository casualty wave.

The one operation this script deliberately does not fake is GitHub's deleted-repository
restore. GitHub restores personal-account repositories through the account UI; creating a
replacement namespace first can sacrifice the original repository object/history path.

After an original repository exists again, this activator restores operating posture:
  * unarchives it;
  * strips the historical ``Z-BACKUP-`` prefix when the clean namespace is free;
  * recognizes GitHub's old-name redirect as the SAME repository identity, not a collision;
  * preserves BOTH histories on a true namespace collision by using ``-recovered-full``;
  * can reactivate the surviving Z-BACKUP repositories in the same pass;
  * has a dedicated forensic lane for exact forensic-recovery execution;
  * emits a machine-readable receipt.

It never deletes a repository, force-pushes, overwrites an existing repository, or merges
histories without an explicit later reconciliation step.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
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
    "Z-BACKUP-apex-mcp-server",
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
    "Z-BACKUP-desktop-commander-mcp",
    "Z-BACKUP-Federal-Forensic-Framework",
    "Z-BACKUP-Federal-Forensic-MCP-Master",
    "Z-BACKUP-android-forensics",
    "Z-BACKUP-forensic_transcriber",
    "Z-BACKUP-mba-desktop-forensics",
    "Z-BACKUP-sharepoint-forensic-mastermind",
    "OmniRoute",
    "Computer_User",
    "HydraPersonalDefenseSource",
    "grok-build",
]

# These survived the deletion wave but remain archived under the destructive Z-BACKUP
# lifecycle naming. Full activation removes that lifecycle posture too.
SURVIVING_Z_BACKUPS = [
    "Z-BACKUP-mastermind-colossus",
    "Z-BACKUP-apex-vault",
    "Z-BACKUP-apex-gateway",
    "Z-BACKUP-apex-command-center",
    "Z-BACKUP-APEX-NEXUS-AUTOMATION",
    "Z-BACKUP-apex-ci-guardian",
    "Z-BACKUP-aspen-grove-unified",
    "Z-BACKUP-FEDERAL-FORENSIC-REPAIR-OMNIBUS",
    "Z-BACKUP-aspen-grove-omni-bridge",
    "Z-BACKUP-APEX-LEGAL-WARFARE-ORCHESTRATOR",
    "Z-BACKUP-file-commander",
    "Z-BACKUP-Digital-Forensics-Report",
    "Z-BACKUP-digital-forensics-labs",
    "Z-BACKUP-ios-forensics-mcp",
    "Z-BACKUP-DesktopCommanderMCP",
    "Z-BACKUP-Elcomsoft-Phone-Breaker-Mobile-Forensic-Analysis",
]

# Exact forensic-recovery frontier reconstructed from the 2026-08-10 Monolith inventory,
# the 2026-06-19 estate webhook wiring log, and current authenticated GitHub readback.
# This lane intentionally includes deleted originals and surviving renamed originals.
FORENSIC_TARGETS = [
    "Z-BACKUP-FEDERAL-FORENSIC-REPAIR-OMNIBUS",
    "Z-BACKUP-Digital-Forensics-Report",
    "Z-BACKUP-digital-forensics-labs",
    "Z-BACKUP-ios-forensics-mcp",
    "Z-BACKUP-DesktopCommanderMCP",
    "Z-BACKUP-Elcomsoft-Phone-Breaker-Mobile-Forensic-Analysis",
    "Z-BACKUP-Federal-Forensic-Framework",
    "Z-BACKUP-Federal-Forensic-MCP-Master",
    "Z-BACKUP-android-forensics",
    "Z-BACKUP-forensic_transcriber",
    "Z-BACKUP-mba-desktop-forensics",
    "Z-BACKUP-sharepoint-forensic-mastermind",
    "Z-BACKUP-desktop-commander-mcp",
    "Z-BACKUP-apex-mcp-server",
    "Z-BACKUP-ULTIMATE-REPAIR-APEX",
]

_TOKEN: str | None = None
_TOKEN_SOURCE: str | None = None


@dataclass
class Result:
    source: str
    desired: str
    state: str
    final_name: str | None = None
    source_archived_before: bool | None = None
    target_collision: bool = False
    repository_id: int | None = None
    detail: str | None = None


def resolve_token() -> tuple[str, str]:
    """Resolve an authenticated user credential without ever printing it.

    Priority is an explicit cross-repository PAT, then a local ``gh auth token``. The
    workflow-scoped GITHUB_TOKEN is last because it normally cannot administer sibling
    repositories; it remains useful for casualty detection in public contexts.
    """
    global _TOKEN, _TOKEN_SOURCE
    if _TOKEN:
        return _TOKEN, _TOKEN_SOURCE or "cached"

    for env_name in ("GH_PAT", "APEX_PRIVATE_READ_TOKEN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            _TOKEN, _TOKEN_SOURCE = value, env_name
            return value, env_name

    if not os.environ.get("GITHUB_ACTIONS") and shutil.which("gh"):
        proc = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        value = proc.stdout.strip()
        if proc.returncode == 0 and value:
            _TOKEN, _TOKEN_SOURCE = value, "gh auth token"
            return value, "gh auth token"

    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if value:
        _TOKEN, _TOKEN_SOURCE = value, "GITHUB_TOKEN"
        return value, "GITHUB_TOKEN"

    raise SystemExit("No authenticated GitHub credential found (GH_PAT, local gh auth, or GITHUB_TOKEN)")


def api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any, dict[str, str]]:
    auth, _ = resolve_token()
    url = f"{API}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {auth}",
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


def patch_repo(repo_name: str, **changes: Any) -> tuple[int, Any]:
    """Patch repository metadata without colliding with GitHub's ``name`` field."""
    status, payload, _ = api(
        "PATCH",
        f"/repos/{OWNER}/{urllib.parse.quote(repo_name, safe='')}",
        changes,
    )
    return status, payload


def desired_name(source: str) -> str:
    prefix = "Z-BACKUP-"
    return source[len(prefix):] if source.startswith(prefix) else source


def repository_id(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("id")
    return value if isinstance(value, int) and value > 0 else None


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

    current_id = repository_id(current)
    archived_before = bool(current.get("archived"))
    actual_name = str(current.get("name") or source)

    # Non-Z casualties retain their exact restored name; just reactivate.
    if desired == source:
        if archived_before:
            pstatus, payload = patch_repo(actual_name, archived=False)
            if pstatus != 200:
                return Result(
                    source,
                    desired,
                    "UNARCHIVE_FAILED",
                    source_archived_before=True,
                    repository_id=current_id,
                    detail=f"HTTP {pstatus}: {payload}",
                )
        return Result(
            source,
            desired,
            "ACTIVE",
            final_name=actual_name,
            source_archived_before=archived_before,
            repository_id=current_id,
        )

    target_status, target = repo(desired)
    target_id = repository_id(target)

    # GitHub keeps old repository names as redirects after a rename. A GET of the clean
    # historical name can therefore return HTTP 200 for the exact SAME repository object
    # that currently bears Z-BACKUP-. That is not a collision and must never be split into
    # a synthetic -recovered-full identity.
    same_identity_redirect = (
        target_status == 200
        and current_id is not None
        and target_id is not None
        and current_id == target_id
    )

    if target_status == 404 or same_identity_redirect:
        pstatus, payload = patch_repo(actual_name, name=desired, archived=False)
        if pstatus == 200:
            state = "RENAMED_REDIRECT_ACTIVE" if same_identity_redirect else "RENAMED_ACTIVE"
            return Result(
                source,
                desired,
                state,
                final_name=payload.get("name", desired),
                source_archived_before=archived_before,
                repository_id=current_id,
                detail=(
                    "Clean-name lookup resolved to the same repository ID; original object/history preserved."
                    if same_identity_redirect
                    else "Original repository object/history preserved under clean active identity."
                ),
            )
        return Result(
            source,
            desired,
            "RENAME_FAILED",
            source_archived_before=archived_before,
            repository_id=current_id,
            detail=f"HTTP {pstatus}: {payload}",
        )

    if target_status != 200:
        return Result(
            source,
            desired,
            "TARGET_LOOKUP_FAILED",
            source_archived_before=archived_before,
            repository_id=current_id,
            detail=f"HTTP {target_status}: {target}",
        )

    # A different numeric repository ID at the clean namespace is a true collision.
    # Never replace it. Both histories survive; capability reconciliation comes later.
    recovered = available_recovery_name(desired)
    pstatus, payload = patch_repo(actual_name, name=recovered, archived=False)
    if pstatus == 200:
        return Result(
            source,
            desired,
            "RENAMED_COLLISION_PRESERVED",
            final_name=payload.get("name", recovered),
            source_archived_before=archived_before,
            target_collision=True,
            repository_id=current_id,
            detail=(
                f"Existing {OWNER}/{desired} has repository ID {target_id}; "
                "restored original history activated separately for capability reconciliation"
            ),
        )
    return Result(
        source,
        desired,
        "COLLISION_RENAME_FAILED",
        source_archived_before=archived_before,
        target_collision=True,
        repository_id=current_id,
        detail=f"HTTP {pstatus}: {payload}",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="Also reactivate/rename every surviving Z-BACKUP repository, not only deleted-wave casualties.",
    )
    mode.add_argument(
        "--forensics",
        action="store_true",
        help="Recover the exact forensic backup frontier only: deleted originals plus surviving renamed originals.",
    )
    return p.parse_args()


def selected_targets(args: argparse.Namespace) -> list[str]:
    if args.forensics or os.environ.get("RECOVERY_FORENSICS") == "1":
        return list(dict.fromkeys(FORENSIC_TARGETS))
    targets = list(CASUALTIES)
    if args.full or os.environ.get("RECOVERY_FULL") == "1":
        targets.extend(name for name in SURVIVING_Z_BACKUPS if name not in targets)
    return targets


def main() -> int:
    args = parse_args()
    _, token_source = resolve_token()
    targets = selected_targets(args)

    results: list[Result] = []
    for name in targets:
        result = activate(name)
        results.append(result)
        print(json.dumps(asdict(result), sort_keys=True))
        time.sleep(0.08)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.state] = counts.get(r.state, 0) + 1

    receipt = {
        "schema": "glaciereq.repository-recovery.v3",
        "owner": OWNER,
        "token_source": token_source,
        "full_activation": bool(args.full or os.environ.get("RECOVERY_FULL") == "1"),
        "forensic_activation": bool(args.forensics or os.environ.get("RECOVERY_FORENSICS") == "1"),
        "casualty_count": len(CASUALTIES),
        "surviving_z_backup_count": len(SURVIVING_Z_BACKUPS),
        "forensic_target_count": len(FORENSIC_TARGETS),
        "target_count": len(targets),
        "counts": counts,
        "results": [asdict(r) for r in results],
    }
    out = os.environ.get("RECOVERY_RECEIPT", "repo-recovery-receipt.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"receipt": out, "counts": counts, "token_source": token_source}, sort_keys=True))

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
