#!/usr/bin/env python3
"""Publish a verified compiled legal brief to Supabase, Notion, and MotherDuck."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SHA64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_PDF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf$")
MAX_PDF_BYTES = 50 * 1024 * 1024


def _http_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(8192)


def _load_verified_brief(staging_dir: Path) -> tuple[dict, bytes]:
    meta_path = staging_dir / "metadata.json"
    pdf_path = staging_dir / "brief.pdf"
    if meta_path.is_symlink() or not meta_path.is_file():
        raise SystemExit("brief metadata.json is missing or unsafe")
    if pdf_path.is_symlink() or not pdf_path.is_file():
        raise SystemExit("compiled brief.pdf is missing or unsafe")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise SystemExit("metadata.json must be an object")

    pdf_name = str(metadata.get("pdf_name") or "")
    if not SAFE_PDF_NAME.fullmatch(pdf_name):
        raise SystemExit(f"unsafe pdf_name in metadata: {pdf_name!r}")

    pdf_bytes = pdf_path.read_bytes()
    if not pdf_bytes or len(pdf_bytes) > MAX_PDF_BYTES:
        raise SystemExit("compiled brief.pdf size is invalid or exceeds bound")

    expected_sha = str(metadata.get("pdf_sha256") or "").lower()
    actual_sha = hashlib.sha256(pdf_bytes).hexdigest()
    if not SHA64.fullmatch(expected_sha) or actual_sha != expected_sha:
        raise SystemExit("compiled brief.pdf SHA-256 mismatch against metadata")

    return metadata, pdf_bytes


def _upload_supabase(
    supabase_url: str,
    service_key: str,
    storage_path: str,
    pdf_bytes: bytes,
) -> str:
    base = supabase_url.rstrip("/")
    encoded_path = urllib.parse.quote(storage_path, safe="/")
    object_url = f"{base}/storage/v1/object/{encoded_path}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/pdf",
        "User-Agent": "GlacierEQ-legal-brief-publisher",
    }
    status, body = _http_request("POST", object_url, headers, pdf_bytes)
    if status in (400, 409):
        status, body = _http_request("PUT", object_url, headers, pdf_bytes)
    if status not in range(200, 300):
        detail = body.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Supabase upload failed HTTP {status}: {detail}")
    return f"{base}/storage/v1/object/public/{encoded_path}"


def _sync_notion(
    *,
    notion_token: str,
    database_id: str,
    case_name: str,
    pdf_url: str,
    timestamp: str,
    branch: str,
    word_count: int,
    commit_url: str,
    repository: str,
) -> None:
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
        "User-Agent": "GlacierEQ-legal-brief-publisher",
    }
    safe_db = urllib.parse.quote(database_id.strip(), safe="")
    query_payload = json.dumps(
        {
            "filter": {
                "property": "Name",
                "title": {"equals": case_name},
            }
        }
    ).encode("utf-8")
    status, raw = _http_request(
        "POST",
        f"https://api.notion.com/v1/databases/{safe_db}/query",
        headers,
        query_payload,
    )
    if status not in range(200, 300):
        detail = raw.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Notion query failed HTTP {status}: {detail}")

    results = json.loads(raw.decode("utf-8")).get("results") or []
    page_id = str(results[0].get("id") or "") if results else ""

    shared_props: dict[str, object] = {
        "PDF URL": {"url": pdf_url or None},
        "Last Built": {"date": {"start": timestamp}},
        "Branch": {"rich_text": [{"text": {"content": branch}}]},
        "Word Count": {"number": word_count},
        "Commit": {"url": commit_url},
        "Status": {"select": {"name": "Built"}},
    }
    if page_id:
        safe_page = urllib.parse.quote(page_id, safe="-")
        patch_body = json.dumps({"properties": shared_props}).encode("utf-8")
        p_status, p_raw = _http_request(
            "PATCH",
            f"https://api.notion.com/v1/pages/{safe_page}",
            headers,
            patch_body,
        )
        if p_status not in range(200, 300):
            raise RuntimeError(
                f"Notion page update failed HTTP {p_status}: "
                f"{p_raw.decode('utf-8', errors='replace')[:500]}"
            )
    else:
        create_props = {
            "Name": {"title": [{"text": {"content": case_name}}]},
            "Repository": {"rich_text": [{"text": {"content": repository}}]},
            **shared_props,
        }
        create_body = json.dumps(
            {"parent": {"database_id": database_id.strip()}, "properties": create_props}
        ).encode("utf-8")
        c_status, c_raw = _http_request(
            "POST",
            "https://api.notion.com/v1/pages",
            headers,
            create_body,
        )
        if c_status not in range(200, 300):
            raise RuntimeError(
                f"Notion page create failed HTTP {c_status}: "
                f"{c_raw.decode('utf-8', errors='replace')[:500]}"
            )


def _log_motherduck(
    *,
    token: str,
    run_id: str,
    repository: str,
    branch: str,
    pdf_name: str,
    pdf_url: str,
    word_count: int,
    commit_sha: str,
) -> None:
    import duckdb

    con = duckdb.connect(f"md:legal_briefs?motherduck_token={token}")
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS brief_builds (
                build_id      VARCHAR,
                repo          VARCHAR,
                branch        VARCHAR,
                pdf_name      VARCHAR,
                pdf_url       VARCHAR,
                word_count    INTEGER,
                built_at      TIMESTAMP,
                commit_sha    VARCHAR
            )
            """
        )
        con.execute(
            "INSERT INTO brief_builds VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                repository,
                branch,
                pdf_name,
                pdf_url,
                word_count,
                datetime.now(timezone.utc),
                commit_sha,
            ],
        )
    finally:
        con.close()


def publish_brief(staging_dir: Path) -> int:
    metadata, pdf_bytes = _load_verified_brief(staging_dir.resolve())
    pdf_name = str(metadata["pdf_name"])
    word_count = int(metadata.get("word_count") or 0)
    case_name = str(metadata.get("case_name") or "").strip() or pdf_name[:-4]

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "")
    commit_sha = os.environ.get("GITHUB_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit_url = f"https://github.com/{repository}/commit/{commit_sha}"
    storage_path = f"case-briefs/{repository}/{branch}/{pdf_name}"

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    pdf_url = ""
    if supabase_url and supabase_key:
        pdf_url = _upload_supabase(
            supabase_url, supabase_key, storage_path, pdf_bytes
        )

    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if notion_token and notion_db:
        _sync_notion(
            notion_token=notion_token,
            database_id=notion_db,
            case_name=case_name,
            pdf_url=pdf_url,
            timestamp=timestamp,
            branch=branch,
            word_count=word_count,
            commit_url=commit_url,
            repository=repository,
        )

    motherduck_token = os.environ.get("MOTHERDUCK_TOKEN", "").strip()
    if motherduck_token:
        _log_motherduck(
            token=motherduck_token,
            run_id=run_id,
            repository=repository,
            branch=branch,
            pdf_name=pdf_name,
            pdf_url=pdf_url,
            word_count=word_count,
            commit_sha=commit_sha,
        )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        pdf_cell = f"[{pdf_name}]({pdf_url})" if pdf_url else pdf_name
        lines = [
            "## Legal Brief Pipeline Complete",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| **PDF** | {pdf_cell} |",
            f"| **Words** | {word_count} |",
            f"| **Branch** | {branch} |",
            f"| **Commit** | {commit_sha} |",
            f"| **Built** | {timestamp} |",
        ]
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", required=True)
    args = parser.parse_args()
    return publish_brief(Path(args.staging_dir))


if __name__ == "__main__":
    raise SystemExit(main())
