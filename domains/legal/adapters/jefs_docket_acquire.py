"""Governed read-only JEFS acquisition for the private DOCKETS repository.

The adapter executes the repository-owned extractor, preserves only court/docket
artifacts in a new immutable-style acquisition folder, and never returns or logs
credentials. It is intentionally scoped to 1FDV-23-0001009 and seq 223-226.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import apex_catalog_runner as catalog

EXPECTED_ACTION = "docket-sync"
EXPECTED_ADAPTERS = {"validate", "jefs-docket-acquire"}
EXPECTED_REPOSITORY = "GlacierEQ/DOCKETS"
CASE_ID = "1FDV-23-0001009"
SEQUENCES = (223, 224, 225, 226)
BRANCH = "master"
GITHUB_API = "https://api.github.com"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _github_put(token: str, repo_path: str, data: bytes, message: str) -> str:
    url = f"{GITHUB_API}/repos/{EXPECTED_REPOSITORY}/contents/{urllib.parse.quote(repo_path, safe='/')}"
    payload = json.dumps(
        {
            "message": message,
            "content": base64.b64encode(data).decode("ascii"),
            "branch": BRANCH,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="PUT",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "GlacierEQ-APEX-JEFS-Acquirer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"private evidence write failed HTTP {exc.code}: {detail[:1000]}") from exc
    commit_sha = str((body.get("commit") or {}).get("sha") or "")
    if len(commit_sha) != 40:
        raise RuntimeError("private evidence write returned no commit SHA")
    return commit_sha


def _find_chrome() -> str:
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        value = shutil.which(name)
        if value:
            return value
    return ""


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()

    if plan.get("action") != EXPECTED_ACTION or plan.get("adapter") not in EXPECTED_ADAPTERS:
        return catalog.write_result(plan, result_path, "blocked", reason="JEFS adapter contract mismatch")
    if plan.get("source_repo") != EXPECTED_REPOSITORY:
        return catalog.write_result(plan, result_path, "blocked", reason="JEFS adapter repository mismatch")

    extractor = workspace / "TOOLS" / "jefs_docket_extractor_ci.js"
    legacy_credential_source = workspace / "TOOLS" / "jefs_docket_extractor.js"
    if not extractor.is_file() or not legacy_credential_source.is_file():
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="repository-owned JEFS extractor inputs are missing",
        )

    evidence_token = os.environ.get("APEX_EVIDENCE_WRITE_TOKEN", "")
    if not evidence_token:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="private evidence write token is unavailable",
        )

    chrome = _find_chrome()
    if not chrome:
        return catalog.write_result(plan, result_path, "blocked", reason="Chrome/Chromium is unavailable")
    if not shutil.which("node") or not shutil.which("npm"):
        return catalog.write_result(plan, result_path, "blocked", reason="Node/npm is unavailable")

    with tempfile.TemporaryDirectory(prefix="apex-jefs-") as tmp:
        temp = Path(tmp)
        npm_prefix = temp / "node"
        output_dir = temp / "evidence"
        output_dir.mkdir(parents=True, exist_ok=True)

        install = subprocess.run(
            ["npm", "install", "--prefix", str(npm_prefix), "--no-save", "--ignore-scripts", "puppeteer-core@24.16.0"],
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
        if install.returncode != 0:
            return catalog.write_result(
                plan,
                result_path,
                "failed",
                reason="puppeteer-core installation failed",
                install_output_sha256=_sha256((install.stdout or "").encode()),
            )

        env = {
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", ""),
            "NODE_PATH": str(npm_prefix / "node_modules"),
            "CHROME_BIN": chrome,
            "JEFS_CASE_ID": CASE_ID,
            "JEFS_OUTPUT_DIR": str(output_dir),
        }
        proc = subprocess.run(
            ["node", str(extractor)],
            cwd=workspace,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
            check=False,
        )
        output = proc.stdout or ""

        manifest_path = output_dir / "manifest.json"
        manifest = {}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}

        downloads = []
        for item in manifest.get("downloads", []) if isinstance(manifest, dict) else []:
            if not isinstance(item, dict):
                continue
            seq = item.get("seqNo")
            if seq not in SEQUENCES:
                continue
            filename = str(item.get("filename") or "")
            file_path = output_dir / filename
            observed_hash = ""
            if file_path.is_file():
                observed_hash = _sha256(file_path.read_bytes())
            downloads.append(
                {
                    "seqNo": seq,
                    "status": item.get("status"),
                    "contentType": item.get("contentType"),
                    "isPdf": bool(item.get("isPdf")),
                    "bytes": item.get("bytes"),
                    "sha256": observed_hash or item.get("sha256"),
                    "filename": filename,
                }
            )

        docket_rows_path = output_dir / "docket_rows_223_226.json"
        docket_rows = []
        if docket_rows_path.is_file():
            try:
                docket_rows = json.loads(docket_rows_path.read_text(encoding="utf-8"))
            except Exception:
                docket_rows = []

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        acquisition_root = f"{CASE_ID}/JEFS_ACQUISITIONS/{stamp}_{plan['job_id']}"
        source_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "")
        receipt = {
            "schema_version": "1.0",
            "job_id": plan.get("job_id"),
            "action": EXPECTED_ACTION,
            "case_id": CASE_ID,
            "requested_sequences": list(SEQUENCES),
            "source_repo": EXPECTED_REPOSITORY,
            "source_sha": source_sha,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "extractor_exit_code": proc.returncode,
            "extractor_output_sha256": _sha256(output.encode()),
            "docket_rows_223_226": docket_rows,
            "downloads": downloads,
            "credential_material_in_receipt": False,
            "write_policy": "new_path_only_no_overwrite",
        }

        evidence_commits = []
        upload_errors = []
        try:
            for item in downloads:
                if not item.get("isPdf"):
                    continue
                file_path = output_dir / str(item["filename"])
                if not file_path.is_file():
                    continue
                repo_path = f"{acquisition_root}/{item['filename']}"
                commit = _github_put(
                    evidence_token,
                    repo_path,
                    file_path.read_bytes(),
                    f"evidence(jefs): acquire seq {item['seqNo']} for {CASE_ID}",
                )
                evidence_commits.append({"path": repo_path, "commit_sha": commit, "sha256": item.get("sha256")})

            if docket_rows_path.is_file():
                repo_path = f"{acquisition_root}/docket_rows_223_226.json"
                commit = _github_put(
                    evidence_token,
                    repo_path,
                    docket_rows_path.read_bytes(),
                    f"evidence(jefs): preserve docket rows 223-226 for {CASE_ID}",
                )
                evidence_commits.append({"path": repo_path, "commit_sha": commit, "sha256": _sha256(docket_rows_path.read_bytes())})

            receipt_bytes = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
            receipt_path = f"{acquisition_root}/ACQUISITION_RECEIPT.json"
            commit = _github_put(
                evidence_token,
                receipt_path,
                receipt_bytes,
                f"evidence(jefs): preserve acquisition receipt for {CASE_ID}",
            )
            evidence_commits.append({"path": receipt_path, "commit_sha": commit, "sha256": _sha256(receipt_bytes)})
        except Exception as exc:
            upload_errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            evidence_token = ""
            os.environ.pop("APEX_EVIDENCE_WRITE_TOKEN", None)

        seq225 = next((item for item in downloads if item.get("seqNo") == 225), None)
        complete = bool(
            proc.returncode == 0
            and docket_rows
            and seq225
            and seq225.get("isPdf")
            and not upload_errors
        )
        return catalog.write_result(
            plan,
            result_path,
            "completed" if complete else "failed",
            case_id=CASE_ID,
            acquisition_root=acquisition_root,
            source_sha=source_sha,
            docket_rows=docket_rows,
            downloads=downloads,
            evidence_commits=evidence_commits,
            upload_errors=upload_errors,
            extractor_exit_code=proc.returncode,
            extractor_output_sha256=_sha256(output.encode()),
            required_gate={
                "docket_rows_present": bool(docket_rows),
                "seq225_official_pdf_acquired": bool(seq225 and seq225.get("isPdf")),
                "private_write_succeeded": not upload_errors,
            },
        )
