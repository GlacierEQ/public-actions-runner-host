from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "action_face_prepare_dependencies.py"
SPEC = importlib.util.spec_from_file_location("action_face_prepare_dependencies", MODULE)
assert SPEC is not None and SPEC.loader is not None
deps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deps)


def write_manifest(root: Path, payload: dict) -> Path:
    path = root / "scripts" / "ci" / "private_dependencies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def valid_manifest() -> dict:
    return {
        "schema": "glaciereq.ci-private-dependencies.v1",
        "dependencies": [
            {
                "repository": "GlacierEQ/AKOS",
                "source_ref": "main",
                "source_sha": "a" * 40,
            },
            {
                "repository": "GlacierEQ/pro-code",
                "source_ref": "main",
                "source_sha": "b" * 40,
            },
        ],
    }


def test_loads_bounded_exact_dependency_manifest(tmp_path: Path) -> None:
    write_manifest(tmp_path, valid_manifest())
    result = deps.load_dependency_spec(
        tmp_path,
        "scripts/ci/private_dependencies.json",
    )
    assert [item["repository"] for item in result] == [
        "GlacierEQ/AKOS",
        "GlacierEQ/pro-code",
    ]
    assert all(len(item["source_sha"]) == 40 for item in result)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"schema": "wrong"}),
        lambda p: p["dependencies"].append(p["dependencies"][0].copy()),
        lambda p: p["dependencies"][0].update({"repository": "OtherOrg/AKOS"}),
        lambda p: p["dependencies"][0].update({"source_ref": "../main"}),
        lambda p: p["dependencies"][0].update({"source_sha": "main"}),
    ],
)
def test_rejects_untrusted_dependency_manifest(tmp_path: Path, mutate) -> None:
    payload = valid_manifest()
    mutate(payload)
    write_manifest(tmp_path, payload)
    with pytest.raises(ValueError):
        deps.load_dependency_spec(
            tmp_path,
            "scripts/ci/private_dependencies.json",
        )


def test_rejects_dependency_manifest_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    outside.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    with pytest.raises(ValueError):
        deps.load_dependency_spec(tmp_path, "../outside.json")


def test_staged_manifest_contains_no_credentials(tmp_path: Path) -> None:
    items = [
        {
            "repository": "GlacierEQ/AKOS",
            "source_ref": "main",
            "source_sha": "a" * 40,
            "path": str(tmp_path / "AKOS"),
        }
    ]
    output = tmp_path / "staged.json"
    deps.write_staged_manifest(output, items)
    parsed = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(parsed).lower()
    assert parsed["schema"] == "glaciereq.ci-staged-dependencies.v1"
    assert "token" not in serialized
    assert "credential" not in serialized
