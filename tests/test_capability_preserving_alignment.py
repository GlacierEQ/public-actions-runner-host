import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alignment = load_module("action_face_alignment", "scripts/action_face_alignment.py")
topology = load_module("public_runner_team_map", "scripts/public_runner_team_map.py")


def test_action_face_drift_is_reported_without_disabling_continuation() -> None:
    expected = {
        "repository": "GlacierEQ/public-actions-runner-host",
        "repository_id": 1265621488,
        "owner": "GlacierEQ",
        "owner_id": 194243768,
        "required_visibility": "public",
        "required_default_branch": "main",
        "required_archived": False,
        "required_disabled": False,
        "required_fork": False,
    }

    report = alignment.assess_alignment(
        expected,
        repository="GlacierEQ/other-repository",
        repository_id="1265621488",
        metadata=None,
        lookup_error="identity lookup unavailable: TimeoutError",
    )

    assert report["status"] == "drift_detected"
    assert "environment_repository" in report["mismatches"]
    assert report["continuation"] == "enabled"


def test_action_face_full_metadata_can_report_alignment() -> None:
    expected = alignment.load_expected(ROOT / "config" / "action-face-identity.json")
    metadata = {
        "full_name": expected["repository"],
        "id": expected["repository_id"],
        "owner": {"login": expected["owner"], "id": expected["owner_id"]},
        "visibility": expected["required_visibility"],
        "default_branch": expected["required_default_branch"],
        "archived": expected["required_archived"],
        "disabled": expected["required_disabled"],
        "fork": expected["required_fork"],
    }

    report = alignment.assess_alignment(
        expected,
        repository=expected["repository"],
        repository_id=str(expected["repository_id"]),
        metadata=metadata,
        lookup_error=None,
    )

    assert report["status"] == "aligned"
    assert report["mismatches"] == []
    assert report["continuation"] == "enabled"


def test_runner_topology_is_observable_and_non_paralyzing() -> None:
    config = {
        "execution_mode": "github-hosted",
        "runner_policy": {
            "allowed_runner_prefixes": ["ubuntu-", "windows-", "macos-"],
            "preferred_runner": "ubuntu-latest",
        },
    }

    report = topology.inspect_workflows(config)

    assert report["checked_workflows"] > 0
    assert report["preferred_runner"] == "ubuntu-latest"
    assert report["continuation"] == "enabled"
    assert report["status"] in {"topology_mapped", "topology_expansion_available"}
