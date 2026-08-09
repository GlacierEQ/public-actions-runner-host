"""Publish a result only when its bytes match the post-run integrity lock."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

import apex_pillar_runner as base
from workload_isolation import WorkloadIsolationError, open_checkout, read_regular_file

DIGEST = re.compile(r"^[0-9a-f]{64}$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_PART = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_RECOVERY_FILES = 16
MAX_RECOVERY_BYTES = 4_000_000


def recovery_records(job_id: str, raw: bytes) -> list[tuple[str, bytes]]:
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("verified result is invalid JSON") from error
    if not isinstance(result, dict) or result.get("job_id") not in (None, job_id):
        raise SystemExit("verified result job_id does not match publication request")

    recovery = result.get("recovery_artifacts")
    if recovery is None:
        return []
    if not isinstance(recovery, dict) or recovery.get("status") != "available":
        raise SystemExit("recovery artifact envelope is invalid")

    resolved = str(result.get("resolved_source_sha", "")).lower()
    envelope_resolved = str(recovery.get("resolved_source_sha", "")).lower()
    if not SOURCE_SHA.fullmatch(resolved) or envelope_resolved != resolved:
        raise SystemExit("recovery artifact source SHA does not match verified result")

    files = recovery.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_RECOVERY_FILES:
        raise SystemExit("recovery artifact file set is empty or exceeds its bound")

    seen: set[str] = set()
    records: list[tuple[str, bytes]] = []
    total_bytes = 0
    manifest_files: list[dict[str, object]] = []
    for item in files:
        if not isinstance(item, dict):
            raise SystemExit("recovery artifact record is not an object")
        source_path = str(item.get("path", ""))
        pure = PurePosixPath(source_path)
        if (
            pure.is_absolute()
            or not pure.parts
            or pure.parts[0] != "artifacts"
            or any(part in {"", ".", ".."} or not SAFE_PART.fullmatch(part) for part in pure.parts)
        ):
            raise SystemExit("recovery artifact path is outside the bounded artifact namespace")
        if source_path in seen:
            raise SystemExit("recovery artifact path is duplicated")
        seen.add(source_path)

        content = item.get("content")
        declared_bytes = item.get("bytes")
        declared_digest = str(item.get("sha256", "")).lower()
        if not isinstance(content, str) or not isinstance(declared_bytes, int):
            raise SystemExit("recovery artifact content metadata is invalid")
        payload = content.encode("utf-8")
        if declared_bytes != len(payload):
            raise SystemExit("recovery artifact byte count does not match content")
        if not DIGEST.fullmatch(declared_digest):
            raise SystemExit("recovery artifact digest is invalid")
        actual_digest = hashlib.sha256(payload).hexdigest()
        if actual_digest != declared_digest:
            raise SystemExit("recovery artifact digest does not match content")

        total_bytes += len(payload)
        if total_bytes > MAX_RECOVERY_BYTES:
            raise SystemExit("recovery artifact payload exceeds materialization byte bound")
        remote_path = f"recovery-artifacts/{job_id}/{source_path}"
        records.append((remote_path, payload))
        manifest_files.append(
            {
                "path": source_path,
                "bytes": len(payload),
                "sha256": actual_digest,
                "control_path": remote_path,
            }
        )

    if recovery.get("total_bytes") != total_bytes:
        raise SystemExit("recovery artifact total byte count does not reconcile")

    manifest = {
        "schema_version": "apex-recovery-artifacts/v1",
        "job_id": job_id,
        "resolved_source_sha": resolved,
        "total_bytes": total_bytes,
        "files": manifest_files,
    }
    manifest_payload = (
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    records.append((f"recovery-artifacts/{job_id}/manifest.json", manifest_payload))
    return records


def publish_recovery_records(job_id: str, records: list[tuple[str, bytes]]) -> None:
    if not records:
        return
    token = base.control_token()
    for remote_path, payload in records:
        existing = base.api(remote_path, token, allow_not_found=True)
        if existing is not None:
            if not isinstance(existing, dict) or not isinstance(existing.get("content"), str):
                raise SystemExit("existing recovery artifact record is malformed")
            try:
                existing_payload = base64.b64decode(existing["content"])
            except Exception as error:  # noqa: BLE001
                raise SystemExit("existing recovery artifact content is invalid") from error
            if existing_payload != payload:
                raise SystemExit("immutable recovery artifact path already contains different bytes")
            continue
        request = {
            "message": f"runner: materialize recovery artifact {job_id}",
            "content": base64.b64encode(payload).decode("ascii"),
        }
        base.api(remote_path, token, method="PUT", payload=request)
    print("Verified recovery artifacts materialized in the private control plane.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--expected-file-sha256", required=True)
    args = parser.parse_args()

    expected = args.expected_file_sha256.lower()
    if not DIGEST.fullmatch(expected):
        raise SystemExit("expected result digest is invalid")

    source_argument = Path(args.result)
    source_absolute = Path(os.path.abspath(os.fspath(source_argument)))
    runner_parent = Path(os.path.abspath(".")).parent
    try:
        runner = open_checkout(Path("."), allowed_parent=runner_parent, label="runner")
        with runner:
            result_directory = open_checkout(
                source_absolute.parent,
                allowed_parent=runner,
                label="result directory",
            )
            with result_directory:
                if source_absolute.parent != result_directory.raw_path:
                    raise WorkloadIsolationError(
                        "verified result is outside the canonical result directory"
                    )
                raw = read_regular_file(
                    result_directory,
                    source_absolute.name,
                    max_bytes=base.MAX_RESULT_BYTES,
                )
                runner.assert_path_identity()
                result_directory.assert_path_identity()
    except WorkloadIsolationError as error:
        raise SystemExit(f"verified result boundary failed: {error}") from error

    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise SystemExit("result bytes changed after post-run verification")

    recovery = recovery_records(args.job_id, raw)
    publish_recovery_records(args.job_id, recovery)

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            prefix="apex-verified-result-",
            suffix=".json",
            delete=False,
        ) as handle:
            os.chmod(handle.name, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        base.publish(args.job_id, Path(temporary_path))
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
