"""Validate Monolith documentation structure without exposing private content."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote

import apex_catalog_runner as catalog

EXPECTED_ACTION = "docs.monolith.validate-integrity"
EXPECTED_REPOSITORY = "GlacierEQ/monolith"
EXPECTED_ADAPTER = "validate"
SHA = re.compile(r"^[0-9a-f]{40}$")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
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


def markdown_files(workspace: Path) -> list[Path]:
    return sorted(
        path
        for path in workspace.rglob("*.md")
        if not any(part in SKIP_DIRECTORIES for part in path.relative_to(workspace).parts)
    )


def resolve_link(source: Path, target: str, workspace: Path) -> Path | None:
    clean = unquote(target.strip())
    if not clean or clean.startswith(("#", "http://", "https://", "mailto:")):
        return None
    clean = clean.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return None
    candidate = (source.parent / clean).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        raise ValueError("documentation link escapes the private workspace") from None
    return candidate


def run(plan: dict, workspace: Path, result_path: Path) -> int:
    workspace = workspace.resolve()
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

    missing_required = [path for path in REQUIRED_PATHS if not (workspace / path).is_file()]
    if missing_required:
        return catalog.write_result(
            plan,
            result_path,
            "blocked",
            reason="required Monolith documentation surfaces are missing: "
            + ", ".join(missing_required),
        )

    invalid_json: list[str] = []
    json_files = sorted(
        path
        for path in workspace.rglob("*.json")
        if not any(part in SKIP_DIRECTORIES for part in path.relative_to(workspace).parts)
    )
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid_json.append(path.relative_to(workspace).as_posix())

    broken_links: list[dict[str, str]] = []
    unbalanced_fences: list[str] = []
    documents = markdown_files(workspace)
    internal_link_count = 0
    for path in documents:
        text = path.read_text(encoding="utf-8")
        if text.count("```") % 2:
            unbalanced_fences.append(path.relative_to(workspace).as_posix())
        for match in LINK.finditer(text):
            try:
                candidate = resolve_link(path, match.group(1), workspace)
            except ValueError as error:
                broken_links.append(
                    {
                        "source": path.relative_to(workspace).as_posix(),
                        "target_sha256": hashlib.sha256(match.group(1).encode()).hexdigest(),
                        "reason": str(error),
                    }
                )
                continue
            if candidate is None:
                continue
            internal_link_count += 1
            if not candidate.exists():
                broken_links.append(
                    {
                        "source": path.relative_to(workspace).as_posix(),
                        "target_sha256": hashlib.sha256(match.group(1).encode()).hexdigest(),
                        "reason": "target does not exist",
                    }
                )

    status = "completed" if not (invalid_json or broken_links or unbalanced_fences) else "failed"
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
    )
