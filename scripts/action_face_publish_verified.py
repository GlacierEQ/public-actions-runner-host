"""Publish a result only when its bytes match the post-run integrity lock."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
from pathlib import Path

import apex_pillar_runner as base
from workload_isolation import WorkloadIsolationError, open_checkout, read_regular_file

DIGEST = re.compile(r"^[0-9a-f]{64}$")


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
