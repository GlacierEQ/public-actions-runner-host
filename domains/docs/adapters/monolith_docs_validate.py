"""Validate Monolith documentation structure without exposing private content."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import apex_catalog_runner as catalog
from scripts.workload_isolation import (
    WorkloadIsolationError,
    attest_checkout,
    open_checkout,
    read_relative_regular_file,
    relative_path_kind,
    tracked_relative_files,
)

EXPECTED_ACTION = "docs.monolith.validate-integrity"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "validate"
SHA = re.compile(r"^[0-9a-f]{40}$")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
MAX_DOCUMENT_BYTES = 5_000_000
REQUIRED_PATHS = (
    "README.md",
    "ROADMAP.md",
    "AGENTS.md",
    "status/MONOLITH_COMMAND_ATLAS.md",
    "status/MONOLITH_QUERY_GUIDE.md",
    "catalog/library.json",
    "catalog/monolith_command_atlas.json",
    "schemas/monolith-command-atlas.schema.json",
)
SKIP_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def validate_plan(plan: dict) -> None:
    expected = {
        "pillar": "B",
        "action": EXPECTED_ACTION,
        "adapter": EXPECTED_ADAPTER,
        "task": "validate",
        "source_repo": EXPECTED_REPOSITORY,
        "target_repo": EXPECTED_REPOSITORY,
    }
    for field, value in expected.items():
        if plan.get(field) != value:
            raise ValueError(f"{field} identity mismatch")


def normalize_internal_link(source: str, target: str) -> str | None:
    clean = unquote(target.strip())
    if not clean or clean.startswith(("#", "http://", "https://", "mailto:")):
        return None
    clean = clean.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return None
    target_path = PurePosixPath(clean)
    if target_path.is_absolute() or "\\" in clean:
        raise ValueError("documentation link escapes the private workspace")

    normalized: list[str] = []
    combined = PurePosixPath(source).parent / target_path
    for part in combined.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized:
                raise ValueError("documentation link escapes the private workspace")
            normalized.pop()
            continue
        normalized.append(part)
    if not normalized:
        raise ValueError("documentation link does not resolve to a workspace entry")
    return PurePosixPath(*normalized).as_posix()


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    result_path = result_path.resolve()
    try:
        validate_plan(plan)
    except ValueError as error:
        return catalog.write_result(plan, result_path, "blocked", reason=str(error))

    resolved_sha = os.environ.get("APEX_RESOLVED_SOURCE_SHA", "").lower()
    if not SHA.fullmatch(resolved_sha):
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="resolved source SHA is unavailable or invalid",
        )

    try:
        checkout = open_checkout(workspace, label="Monolith docs workload")
    except WorkloadIsolationError as error:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason=f"documentation boundary failed before validation: {error}",
        )

    with checkout:
        try:
            pre_attestation = attest_checkout(checkout, resolved_sha)
            missing_required = [
                path
                for path in REQUIRED_PATHS
                if relative_path_kind(checkout, path) != "file"
            ]
            if missing_required:
                return catalog.write_result(
                    plan,
                    result_path,
                    "blocked",
                    reason="required Monolith documentation surfaces are missing: "
                    + ", ".join(missing_required),
                )

            json_files = tracked_relative_files(
                checkout,
                suffixes={".json"},
                skip_parts=SKIP_DIRECTORIES,
            )
            documents = tracked_relative_files(
                checkout,
                suffixes={".md"},
                skip_parts=SKIP_DIRECTORIES,
            )

            invalid_json: list[str] = []
            for relative in json_files:
                try:
                    raw = read_relative_regular_file(
                        checkout,
                        relative,
                        max_bytes=MAX_DOCUMENT_BYTES,
                    )
                    json.loads(raw.decode("utf-8"))
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    WorkloadIsolationError,
                ):
                    invalid_json.append(relative)

            broken_links: list[dict[str, str]] = []
            unbalanced_fences: list[str] = []
            internal_link_count = 0
            for relative in documents:
                try:
                    raw = read_relative_regular_file(
                        checkout,
                        relative,
                        max_bytes=MAX_DOCUMENT_BYTES,
                    )
                    text = raw.decode("utf-8")
                except (UnicodeDecodeError, WorkloadIsolationError):
                    unbalanced_fences.append(relative)
                    continue
                if text.count("```") % 2:
                    unbalanced_fences.append(relative)
                for match in LINK.finditer(text):
                    target = match.group(1)
                    try:
                        candidate = normalize_internal_link(relative, target)
                    except ValueError as error:
                        broken_links.append(
                            {
                                "source": relative,
                                "target_sha256": hashlib.sha256(
                                    target.encode()
                                ).hexdigest(),
                                "reason": str(error),
                            }
                        )
                        continue
                    if candidate is None:
                        continue
                    internal_link_count += 1
                    try:
                        kind = relative_path_kind(checkout, candidate)
                    except WorkloadIsolationError:
                        kind = None
                    if kind is None:
                        broken_links.append(
                            {
                                "source": relative,
                                "target_sha256": hashlib.sha256(
                                    target.encode()
                                ).hexdigest(),
                                "reason": "target does not exist",
                            }
                        )

            post_attestation = attest_checkout(checkout, resolved_sha)
        except WorkloadIsolationError as error:
            return catalog.write_result(
                plan,
                result_path,
                "failed",
                reason=f"documentation boundary failed during validation: {error}",
            )

    status = (
        "completed"
        if not (invalid_json or broken_links or unbalanced_fences)
        else "failed"
    )
    return catalog.write_result(
        plan,
        result_path,
        status,
        docs_summary={
            "markdown_files": len(documents),
            "json_files": len(json_files),
            "internal_links_checked": internal_link_count,
            "invalid_json_count": len(invalid_json),
            "broken_link_count": len(broken_links),
            "unbalanced_fence_count": len(unbalanced_fences),
        },
        invalid_json=invalid_json[:50],
        broken_links=broken_links[:50],
        unbalanced_fences=unbalanced_fences[:50],
        workspace_attestation={
            "before": pre_attestation,
            "after": post_attestation,
        },
    )
