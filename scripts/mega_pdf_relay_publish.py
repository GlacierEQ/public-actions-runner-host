#!/usr/bin/env python3
"""Atomically publish verified MEGA-PDF Function Genome artifacts via GitHub Git Data API."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.code.adapters import mega_pdf_function_genome as genome

REPO = "GlacierEQ/MEGA-PDF"
API = f"https://api.github.com/repos/{REPO}"
DEFAULT_BRANCH = "automation/mega-pdf-function-genome-results"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")


class RelayPublishHttpError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def request(
    method: str, path: str, token: str, payload: dict | None = None
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GlacierEQ-mega-pdf-relay-publisher",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RelayPublishHttpError(
            exc.code, f"GitHub API {method} {path} failed: HTTP {exc.code}: {body}"
        ) from exc


def publish_verified_artifacts(
    *,
    output_root: Path,
    source_ref: str,
    source_sha: str,
    run_id: str,
    run_attempt: str,
    result_branch: str = DEFAULT_BRANCH,
) -> dict[str, object]:
    token = os.environ.get("GITHUB_INSTALLATION_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_INSTALLATION_TOKEN is required")
    source_sha = source_sha.lower()
    if not SHA40.fullmatch(source_sha):
        raise SystemExit("--source-sha must be an exact lowercase 40-character SHA")
    if not SAFE_BRANCH.fullmatch(result_branch) or ".." in result_branch:
        raise SystemExit("result_branch is invalid")

    output_root = output_root.resolve()
    verified = genome.verify_generated_outputs(output_root, source_sha)
    report = verified["report"]
    assert isinstance(report, dict)
    summary = report["summary"]
    assert isinstance(summary, dict)

    relay_receipt = {
        "schema_version": "mega-pdf-private-relay/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": REPO,
        "source_ref": source_ref,
        "source_sha": source_sha,
        "execution_repo": "GlacierEQ/public-actions-runner-host",
        "execution_run_id": run_id,
        "execution_attempt": run_attempt,
        "inventory_digest": report["inventory_digest"],
        "receipt_root": report["receipt_root"],
        "receipt_chain_valid": report["receipt_chain_valid"],
        "discovered": summary["discovered"],
        "promoted_to_probed": summary["promoted_to_probed"],
        "blocked": summary["blocked"],
        "approved": summary["approved"],
        "defaults_promoted": summary["defaults_promoted"],
        "parse_failures": len(report.get("parse_failures", [])),
        "scan_issues": len(report.get("scan_issues", [])),
    }
    relay_path = output_root / "artifacts/relay-receipt.json"
    relay_path.write_text(
        json.dumps(relay_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    payload = genome.collect_payload(output_root, source_sha)
    files = payload["files"]
    assert isinstance(files, list)
    entries = [
        {
            "path": str(item["path"]),
            "mode": "100644",
            "type": "blob",
            "content": str(item["content"]),
        }
        for item in files
    ]

    ref_path = f"/git/ref/heads/{result_branch}"
    base_sha: str | None = None
    base_tree: str | None = None
    try:
        ref_obj = request("GET", ref_path, token)
        base_sha = str(ref_obj["object"]["sha"])
        parent_commit = request("GET", f"/git/commits/{base_sha}", token)
        base_tree = str(parent_commit["tree"]["sha"])
    except RelayPublishHttpError as exc:
        if exc.status != 404:
            raise

    tree_payload: dict[str, object] = {"tree": entries}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = request("POST", "/git/trees", token, tree_payload)

    commit_payload: dict[str, object] = {
        "message": f"chore: publish Function Genome receipts for {source_sha}",
        "tree": tree["sha"],
        "parents": [base_sha] if base_sha else [],
    }
    commit = request("POST", "/git/commits", token, commit_payload)
    new_sha = str(commit["sha"])

    if base_sha:
        request(
            "PATCH",
            f"/git/refs/heads/{result_branch}",
            token,
            {"sha": new_sha, "force": False},
        )
    else:
        request(
            "POST",
            "/git/refs",
            token,
            {"ref": f"refs/heads/{result_branch}", "sha": new_sha},
        )

    readback = str(request("GET", ref_path, token)["object"]["sha"])
    if readback != new_sha:
        raise SystemExit(f"READBACK_MISMATCH: expected {new_sha}, got {readback}")

    return {
        "repository": REPO,
        "branch": result_branch,
        "source_sha": source_sha,
        "published_sha": new_sha,
        "paths": [entry["path"] for entry in entries],
        "report": report,
    }


def write_summary(summary_path: Path, report: dict, result_branch: str) -> None:
    summary = report["summary"]
    lines = [
        "## MEGA-PDF Private Verification Relay",
        "",
        f"- Private results branch: `{result_branch}`",
        f"- Discovered: {summary['discovered']}",
        f"- Promoted to PROBED: {summary['promoted_to_probed']}",
        f"- Blocked: {summary['blocked']}",
        f"- Approved: {summary['approved']}",
        f"- Defaults: {summary['defaults_promoted']}",
        f"- Parse failures: {len(report.get('parse_failures', []))}",
        f"- Scan issues: {len(report.get('scan_issues', []))}",
        f"- Inventory digest: `{report['inventory_digest']}`",
        f"- Receipt root: `{report['receipt_root']}`",
        "- Published via GitHub Git Data REST API in an isolated post-execution job.",
    ]
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--result-branch", default=DEFAULT_BRANCH)
    parser.add_argument("--summary-out", default="")
    args = parser.parse_args()

    outcome = publish_verified_artifacts(
        output_root=Path(args.output_root),
        source_ref=args.source_ref,
        source_sha=args.source_sha,
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        result_branch=args.result_branch,
    )
    if args.summary_out:
        report = outcome["report"]
        assert isinstance(report, dict)
        write_summary(Path(args.summary_out), report, args.result_branch)
    print(
        "MEGA_PDF_RELAY_PUBLISH_OK "
        + json.dumps(
            {
                key: value
                for key, value in outcome.items()
                if key != "report"
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
