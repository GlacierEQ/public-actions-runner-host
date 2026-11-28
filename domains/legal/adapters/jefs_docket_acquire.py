"""Governed JEFS acquisition and post-execution publication for GlacierEQ/DOCKETS.

Workload execution (`run`) executes the repository-owned extractor in a token-free
runner and stages bounded court/docket artifacts locally with SHA-256 digests.
Trusted host publication (`publish_staged_evidence`) runs in a separate post-execution
job using a short-lived write token without importing or executing workload code.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import apex_catalog_runner as catalog

EXPECTED_ACTION = "docket-sync"
EXPECTED_ADAPTERS = {"validate", "jefs-docket-acquire"}
EXPECTED_REPOSITORY = "GlacierEQ/DOCKETS"
CASE_ID = "1FDV-23-0001009"
SEQUENCES = (223, 224, 225, 226)
BRANCH = "master"
GITHUB_API = "https://api.github.com"
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_STAGED_FILE_BYTES = 25 * 1024 * 1024
MAX_STAGED_TOTAL_BYTES = 60 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _github_put(token: str, repo_path: str, data: bytes, message: str) -> str:
    url = (
        f"{GITHUB_API}/repos/{EXPECTED_REPOSITORY}/contents/"
        f"{urllib.parse.quote(repo_path, safe='/')}"
    )
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
        raise RuntimeError(
            f"private evidence write failed HTTP {exc.code}: {detail[:1000]}"
        ) from exc
    commit_sha = str((body.get("commit") or {}).get("sha") or "")
    if len(commit_sha) != 40:
        raise RuntimeError("private evidence write returned no commit SHA")
    return commit_sha


def _find_chrome() -> str:
    for name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        value = shutil.which(name)
        if value:
            return value
    return ""


def _default_stage_dir(plan: dict) -> Path:
    job_id = str(plan.get("job_id", "unknown"))
    return (Path.cwd() / ".apex-jefs-staging" / job_id).resolve()


def _safe_read_regular(path: Path, max_bytes: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"staged file is missing or not regular: {path.name}")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"staged file exceeds byte bound: {path.name}")
    return data


def run(
    plan: dict,
    workspace: Path,
    result_path: Path,
    *,
    stage_dir: Path | None = None,
) -> int:
    workspace = workspace.resolve()
    result_path = result_path.resolve()

    if (
        plan.get("action") != EXPECTED_ACTION
        or plan.get("adapter") not in EXPECTED_ADAPTERS
    ):
        return catalog.write_result(
            plan, result_path, "blocked", reason="JEFS adapter contract mismatch"
        )
    if plan.get("source_repo") != EXPECTED_REPOSITORY:
        return catalog.write_result(
            plan, result_path, "blocked", reason="JEFS adapter repository mismatch"
        )

    extractor = workspace / "TOOLS" / "jefs_docket_extractor_ci.js"
    legacy_credential_source = workspace / "TOOLS" / "jefs_docket_extractor.js"
    if not extractor.is_file() or not legacy_credential_source.is_file():
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="repository-owned JEFS extractor inputs are missing",
        )

    # Never expose write tokens to workload execution even if an environment leaked one.
    os.environ.pop("APEX_EVIDENCE_WRITE_TOKEN", None)

    chrome = _find_chrome()
    if not chrome:
        return catalog.write_result(
            plan, result_path, "blocked", reason="Chrome/Chromium is unavailable"
        )
    if not shutil.which("node") or not shutil.which("npm"):
        return catalog.write_result(
            plan, result_path, "blocked", reason="Node/npm is unavailable"
        )

    target_stage = (stage_dir or _default_stage_dir(plan)).resolve()
    if target_stage.exists():
        shutil.rmtree(target_stage)
    target_stage.mkdir(parents=True, mode=0o700)

    with tempfile.TemporaryDirectory(prefix="apex-jefs-") as tmp:
        temp = Path(tmp)
        npm_prefix = temp / "node"
        output_dir = temp / "evidence"
        output_dir.mkdir(parents=True, exist_ok=True)

        clean_npm_env = {
            "HOME": str(temp / "npm-home"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", ""),
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_FUND": "false",
            "NPM_CONFIG_USERCONFIG": os.devnull,
        }
        (temp / "npm-home").mkdir(parents=True, mode=0o700)

        install = subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                str(npm_prefix),
                "--no-save",
                "--ignore-scripts",
                "puppeteer-core@24.16.0",
            ],
            cwd=workspace,
            env=clean_npm_env,
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
            "HOME": str(temp / "extractor-home"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", ""),
            "NODE_PATH": str(npm_prefix / "node_modules"),
            "CHROME_BIN": chrome,
            "JEFS_CASE_ID": CASE_ID,
            "JEFS_OUTPUT_DIR": str(output_dir),
        }
        (temp / "extractor-home").mkdir(parents=True, mode=0o700)
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
        manifest: dict = {}
        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    manifest = parsed
            except Exception:
                manifest = {}

        downloads: list[dict] = []
        staged_files: list[dict] = []
        total_staged = 0

        for item in manifest.get("downloads", []):
            if not isinstance(item, dict):
                continue
            seq = item.get("seqNo")
            if seq not in SEQUENCES:
                continue
            filename = str(item.get("filename") or "")
            if not SAFE_FILENAME.fullmatch(filename):
                continue
            file_path = output_dir / filename
            observed_hash = ""
            if file_path.is_file() and not file_path.is_symlink():
                raw_bytes = file_path.read_bytes()
                if len(raw_bytes) <= MAX_STAGED_FILE_BYTES and (
                    total_staged + len(raw_bytes) <= MAX_STAGED_TOTAL_BYTES
                ):
                    observed_hash = _sha256(raw_bytes)
                    if bool(item.get("isPdf")):
                        (target_stage / filename).write_bytes(raw_bytes)
                        total_staged += len(raw_bytes)
                        staged_files.append(
                            {
                                "filename": filename,
                                "seqNo": seq,
                                "kind": "pdf",
                                "bytes": len(raw_bytes),
                                "sha256": observed_hash,
                            }
                        )
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
        docket_rows: list = []
        if docket_rows_path.is_file() and not docket_rows_path.is_symlink():
            try:
                rows_raw = docket_rows_path.read_bytes()
                parsed_rows = json.loads(rows_raw.decode("utf-8"))
                if isinstance(parsed_rows, list) and (
                    len(rows_raw) <= MAX_STAGED_FILE_BYTES
                ):
                    docket_rows = parsed_rows
                    (target_stage / "docket_rows_223_226.json").write_bytes(rows_raw)
                    total_staged += len(rows_raw)
                    staged_files.append(
                        {
                            "filename": "docket_rows_223_226.json",
                            "kind": "docket_rows",
                            "bytes": len(rows_raw),
                            "sha256": _sha256(rows_raw),
                        }
                    )
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
        receipt_bytes = (
            json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        )
        (target_stage / "ACQUISITION_RECEIPT.json").write_bytes(receipt_bytes)
        staged_files.append(
            {
                "filename": "ACQUISITION_RECEIPT.json",
                "kind": "receipt",
                "bytes": len(receipt_bytes),
                "sha256": _sha256(receipt_bytes),
            }
        )

        staging_manifest = {
            "schema_version": "apex.jefs-staging.v1",
            "job_id": plan.get("job_id"),
            "case_id": CASE_ID,
            "acquisition_root": acquisition_root,
            "source_sha": source_sha,
            "files": staged_files,
        }
        (target_stage / "staging_manifest.json").write_text(
            json.dumps(staging_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        seq225 = next((item for item in downloads if item.get("seqNo") == 225), None)
        acquired_ok = bool(
            proc.returncode == 0
            and docket_rows
            and seq225
            and seq225.get("isPdf")
        )
        return catalog.write_result(
            plan,
            result_path,
            "completed" if acquired_ok else "failed",
            case_id=CASE_ID,
            acquisition_root=acquisition_root,
            source_sha=source_sha,
            docket_rows=docket_rows,
            downloads=downloads,
            evidence_commits=[],
            upload_errors=[],
            extractor_exit_code=proc.returncode,
            extractor_output_sha256=_sha256(output.encode()),
            required_gate={
                "docket_rows_present": bool(docket_rows),
                "seq225_official_pdf_acquired": bool(seq225 and seq225.get("isPdf")),
                "private_write_succeeded": False,
            },
        )


def publish_staged_evidence(
    plan: dict, stage_dir: Path, result_path: Path
) -> int:
    stage_dir = stage_dir.resolve()
    result_path = result_path.resolve()

    if (
        plan.get("action") != EXPECTED_ACTION
        or plan.get("adapter") not in EXPECTED_ADAPTERS
        or plan.get("source_repo") != EXPECTED_REPOSITORY
    ):
        return catalog.write_result(
            plan, result_path, "blocked", reason="JEFS publish contract mismatch"
        )

    evidence_token = os.environ.get("APEX_EVIDENCE_WRITE_TOKEN", "")
    if not evidence_token:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="private evidence write token is unavailable",
        )

    try:
        candidate = json.loads(
            _safe_read_regular(result_path, 4 * 1024 * 1024).decode("utf-8")
        )
        manifest = json.loads(
            _safe_read_regular(
                stage_dir / "staging_manifest.json", 512 * 1024
            ).decode("utf-8")
        )
    except Exception as exc:
        os.environ.pop("APEX_EVIDENCE_WRITE_TOKEN", None)
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason=f"staged JEFS manifest or result invalid: {type(exc).__name__}",
        )

    acquisition_root = str(manifest.get("acquisition_root") or "")
    if not acquisition_root.startswith(f"{CASE_ID}/JEFS_ACQUISITIONS/") or ".." in acquisition_root:
        os.environ.pop("APEX_EVIDENCE_WRITE_TOKEN", None)
        return catalog.write_result(
            plan,
            result_path,
            "failed",
            reason="staged JEFS acquisition_root is outside allowlist",
        )

    evidence_commits: list[dict] = []
    upload_errors: list[str] = []
    total_bytes = 0
    try:
        for entry in manifest.get("files", []):
            if not isinstance(entry, dict):
                raise ValueError("staged file record is malformed")
            filename = str(entry.get("filename") or "")
            if not SAFE_FILENAME.fullmatch(filename):
                raise ValueError(f"unsafe staged filename: {filename}")
            data = _safe_read_regular(stage_dir / filename, MAX_STAGED_FILE_BYTES)
            total_bytes += len(data)
            if total_bytes > MAX_STAGED_TOTAL_BYTES:
                raise ValueError("staged JEFS total bytes exceed bound")
            actual_sha = _sha256(data)
            if actual_sha != str(entry.get("sha256") or ""):
                raise ValueError(f"staged file digest mismatch: {filename}")

            kind = entry.get("kind")
            if kind == "pdf":
                seq = entry.get("seqNo")
                if seq not in SEQUENCES:
                    raise ValueError(f"unapproved sequence: {seq}")
                message = f"evidence(jefs): acquire seq {seq} for {CASE_ID}"
            elif kind == "docket_rows":
                message = f"evidence(jefs): preserve docket rows 223-226 for {CASE_ID}"
            elif kind == "receipt":
                message = f"evidence(jefs): preserve acquisition receipt for {CASE_ID}"
            else:
                raise ValueError(f"unknown staged file kind: {kind}")

            repo_path = f"{acquisition_root}/{filename}"
            commit = _github_put(evidence_token, repo_path, data, message)
            evidence_commits.append(
                {"path": repo_path, "commit_sha": commit, "sha256": actual_sha}
            )
    except Exception as exc:
        upload_errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        evidence_token = ""
        os.environ.pop("APEX_EVIDENCE_WRITE_TOKEN", None)

    downloads = candidate.get("downloads") or []
    docket_rows = candidate.get("docket_rows") or []
    seq225 = next(
        (
            item
            for item in downloads
            if isinstance(item, dict) and item.get("seqNo") == 225
        ),
        None,
    )
    complete = bool(
        candidate.get("extractor_exit_code") == 0
        and docket_rows
        and seq225
        and seq225.get("isPdf")
        and evidence_commits
        and not upload_errors
    )
    return catalog.write_result(
        plan,
        result_path,
        "completed" if complete else "failed",
        case_id=CASE_ID,
        acquisition_root=acquisition_root,
        source_sha=str(candidate.get("source_sha") or ""),
        docket_rows=docket_rows,
        downloads=downloads,
        evidence_commits=evidence_commits,
        upload_errors=upload_errors,
        extractor_exit_code=candidate.get("extractor_exit_code"),
        extractor_output_sha256=candidate.get("extractor_output_sha256", ""),
        required_gate={
            "docket_rows_present": bool(docket_rows),
            "seq225_official_pdf_acquired": bool(seq225 and seq225.get("isPdf")),
            "private_write_succeeded": bool(evidence_commits and not upload_errors),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish")
    pub.add_argument("--plan", required=True)
    pub.add_argument("--stage-dir", required=True)
    pub.add_argument("--result", required=True)
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    return publish_staged_evidence(
        plan, Path(args.stage_dir), Path(args.result)
    )


if __name__ == "__main__":
    raise SystemExit(main())
