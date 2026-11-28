from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
TECTONIC_RELEASE_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    "tectonic%400.15.0/tectonic-0.15.0-x86_64-unknown-linux-gnu.tar.gz"
)
TECTONIC_SHA256 = "875fbbc9ab48560d7776088c608e0beee49197b57ab4a2f6c5385b2c661c842f"
RUNNER_SHA256 = "9e883d210df8c6028aff475475a457d380353f9d01877d51cc01a17b2a91161d"
EXTERNAL_ACTION_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba|da|z)?sh\b",
    re.IGNORECASE | re.DOTALL,
)

TOUCHED_WORKFLOWS = (
    "apex-pillar-runner.yml",
    "mega-pdf-private-relay-core.yml",
    "private-ci-relay.yml",
    "public-relay-harness.yml",
    "crystallization-public-estate-inventory.yml",
    "jefs-docket-acquire.yml",
    "recover-deleted-glaciereq-repos.yml",
    "recover-forensic-backups.yml",
    "legal-brief-pipeline.yml",
)

WORKLOAD_EXECUTION_JOBS = (
    ("apex-pillar-runner.yml", "execute"),
    ("mega-pdf-private-relay-core.yml", "probe-private-mega-pdf"),
    ("private-ci-relay.yml", "verify-private-source"),
    ("public-relay-harness.yml", "execute-synthetic-relay"),
    ("jefs-docket-acquire.yml", "acquire"),
    ("legal-brief-pipeline.yml", "compile-pdf"),
)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, width=10000)


def load_workflow(name: str) -> dict[str, Any]:
    path = WORKFLOWS_DIR / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} did not parse to a mapping"
    return data


def effective_job_permissions(
    workflow: dict[str, Any], job: dict[str, Any]
) -> dict[str, Any]:
    perms = job.get("permissions", workflow.get("permissions", {}))
    assert isinstance(perms, dict)
    return perms


def job_step_names(job: dict[str, Any]) -> list[str]:
    return [
        str(step.get("name", ""))
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]


def find_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in job.get("steps", []):
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"missing step: {name}")


def test_apex_pillar_runner_separates_prepare_execute_and_publish() -> None:
    wf = load_workflow("apex-pillar-runner.yml")
    jobs = wf["jobs"]
    assert set(jobs) == {"prepare", "execute", "publish"}

    prepare = jobs["prepare"]
    execute = jobs["execute"]
    publish = jobs["publish"]

    assert effective_job_permissions(wf, prepare) == {
        "id-token": "write",
        "contents": "read",
    }
    assert effective_job_permissions(wf, execute) == {"contents": "read"}
    assert effective_job_permissions(wf, publish) == {
        "id-token": "write",
        "contents": "read",
    }

    assert execute.get("needs") in ("prepare", ["prepare"])
    assert publish.get("needs") == ["prepare", "execute"]
    assert "always()" in str(publish.get("if", ""))
    assert "needs.prepare.outputs.claimed == 'true'" in str(publish.get("if", ""))

    # prepare must only mint contents=read for workload and revoke both tokens
    workload_mint = find_step(prepare, "Mint one-repository private workload token")
    assert "--permission contents=read" in workload_mint["run"]
    assert "contents=write" not in workload_mint["run"]
    for revoke_name in (
        "Revoke private workload token",
        "Revoke private control token",
    ):
        revoke_step = find_step(prepare, revoke_name)
        assert "always()" in str(revoke_step.get("if", ""))
        assert "scripts/revoke_github_installation_token.py" in revoke_step["run"]

    # execute must hold zero tokens/OIDC and run workload adapters + rebuild_registry.py
    execute_dump = dump_yaml(execute)
    for forbidden in (
        "id-token",
        "GITHUB_INSTALLATION_TOKEN",
        "APEX_CONTROL_TOKEN",
        "APEX_EVIDENCE_WRITE_TOKEN",
        "APEX_WORKLOAD_TOKEN",
        "keymaster_oidc_token.py",
        "library_registry_publish.py",
        "action_face_publish_verified.py",
    ):
        assert forbidden not in execute_dump
    assert "scripts/action_face_legal_router.py" in execute_dump
    assert "python3 scripts/rebuild_registry.py" in execute_dump
    assert "git diff --check -- registry/index.json registry/top_shelf.json" in execute_dump

    # publish must never checkout or execute workload code and mints write post-execution
    publish_dump = dump_yaml(publish)
    assert "rebuild_registry.py" not in publish_dump
    assert "action_face_legal_router.py" not in publish_dump
    assert "library_registry_publish.py" in publish_dump
    assert "action_face_publish_verified.py" in publish_dump
    assert "action_face_postrun_guard.py" in publish_dump
    assert "action_face_pipeline_result.py" in publish_dump


def test_mega_pdf_private_relay_separates_probe_and_publish_jobs() -> None:
    wf = load_workflow("mega-pdf-private-relay-core.yml")
    jobs = wf["jobs"]
    assert set(jobs) == {"probe-private-mega-pdf", "publish-private-mega-pdf"}

    probe = jobs["probe-private-mega-pdf"]
    publish = jobs["publish-private-mega-pdf"]

    assert effective_job_permissions(wf, probe) == {"contents": "read"}
    assert effective_job_permissions(wf, publish) == {"contents": "read"}
    assert publish.get("needs") in (
        "probe-private-mega-pdf",
        ["probe-private-mega-pdf"],
    )

    mint_read = find_step(probe, "Mint repository-scoped MEGA-PDF token")
    assert mint_read["with"]["permission-contents"] == "read"

    checkout_step = find_step(probe, "Checkout selected private MEGA-PDF source")
    assert checkout_step["with"]["persist-credentials"] is False

    names = job_step_names(probe)
    revoke_idx = names.index("Revoke read-only MEGA-PDF token before workload execution")
    for workload_step in (
        "Install focused verification dependency",
        "Compile governed control plane",
        "Run focused governance and ingestion tests",
        "Ingest real private monorepo Function Genome",
    ):
        assert revoke_idx < names.index(workload_step)

    probe_dump = dump_yaml(probe)
    assert "from mega_pdf_control_plane import ProbeReceipt" not in probe_dump
    assert "mega_pdf_function_genome" in probe_dump
    assert "git push" not in probe_dump

    publish_dump = dump_yaml(publish)
    assert "permission-contents: write" in publish_dump
    assert "mega_pdf_relay_publish.py" in publish_dump
    assert "scripts/revoke_github_installation_token.py" in publish_dump
    assert "path: workload" not in publish_dump


def test_private_ci_relay_and_sibling_relays_isolate_credentials() -> None:
    relay = load_workflow("private-ci-relay.yml")
    assert set(relay["jobs"]) == {"stage-private-source", "verify-private-source"}
    stage = relay["jobs"]["stage-private-source"]
    verify = relay["jobs"]["verify-private-source"]
    assert effective_job_permissions(relay, stage) == {
        "contents": "read",
        "id-token": "write",
    }
    assert effective_job_permissions(relay, verify) == {"contents": "read"}
    assert verify.get("needs") in ("stage-private-source", ["stage-private-source"])
    assert "scripts/revoke_github_installation_token.py" in dump_yaml(stage)
    assert "keymaster_oidc_token.py" not in dump_yaml(verify)
    assert "id-token" not in dump_yaml(verify)

    harness = load_workflow("public-relay-harness.yml")
    assert set(harness["jobs"]) == {"stage-relay-fixture", "execute-synthetic-relay"}
    exec_harness = dump_yaml(harness["jobs"]["execute-synthetic-relay"])
    assert "GH_PAT" not in exec_harness
    assert "APEX_PRIVATE_READ_TOKEN" not in exec_harness

    inventory = load_workflow("crystallization-public-estate-inventory.yml")
    assert "persist-credentials: true" not in (
        WORKFLOWS_DIR / "crystallization-public-estate-inventory.yml"
    ).read_text(encoding="utf-8")
    assert set(inventory["jobs"]) == {"scan-inventory", "publish-inventory"}
    assert effective_job_permissions(
        inventory, inventory["jobs"]["scan-inventory"]
    ) == {"contents": "read"}

    jefs = load_workflow("jefs-docket-acquire.yml")
    assert set(jefs["jobs"]) == {"prepare", "acquire", "publish"}
    assert effective_job_permissions(jefs, jefs["jobs"]["acquire"]) == {
        "contents": "read"
    }
    assert "APEX_EVIDENCE_WRITE_TOKEN" not in dump_yaml(jefs["jobs"]["acquire"])


def test_recovery_workflow_denies_secrets_on_pr_and_gates_environment() -> None:
    wf = load_workflow("recover-deleted-glaciereq-repos.yml")
    jobs = wf["jobs"]
    assert set(jobs) == {"verify-recovery-script", "recover-and-reactivate"}

    verify_pr = jobs["verify-recovery-script"]
    assert verify_pr.get("if") == "github.event_name == 'pull_request'"
    assert effective_job_permissions(wf, verify_pr) == {"contents": "read"}
    verify_dump = dump_yaml(verify_pr)
    assert "secrets." not in verify_dump
    assert "GH_PAT" not in verify_dump
    assert "py_compile" in verify_dump
    assert "pytest" in verify_dump

    recover = jobs["recover-and-reactivate"]
    recover_if = str(recover.get("if", ""))
    assert "github.event_name != 'pull_request'" in recover_if
    assert "github.ref == 'refs/heads/main'" in recover_if
    assert recover.get("environment") == "repository-recovery"
    for step in recover.get("steps", []):
        if isinstance(step, dict) and str(step.get("uses", "")).startswith(
            "actions/checkout@"
        ):
            assert step.get("with", {}).get("persist-credentials") is False


def test_self_hosted_files_retired_and_hardened_in_archive() -> None:
    assert not (ROOT / "Dockerfile").exists()
    assert not (ROOT / "entrypoint.sh").exists()

    archive = ROOT / "legacy" / "retired-self-hosted-runner"
    dockerfile = (archive / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (archive / "entrypoint.sh").read_text(encoding="utf-8")
    readme = (archive / "README.md").read_text(encoding="utf-8")

    assert not PIPE_TO_SHELL.search(dockerfile)
    assert "drop.yannick.io" not in dockerfile
    assert TECTONIC_SHA256 in dockerfile
    assert RUNNER_SHA256 in dockerfile
    assert "sha256sum --check" in dockerfile
    assert "duckdb==" in dockerfile
    assert "--ephemeral" in entrypoint
    assert "rm -rf" in entrypoint
    assert "ubuntu-latest" in readme

    license_file = ROOT / "LICENSE"
    assert license_file.is_file()
    assert "Apache License" in license_file.read_text(encoding="utf-8")
    assert "Version 2.0" in license_file.read_text(encoding="utf-8")

    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ubuntu-latest" in root_readme
    assert "legacy/retired-self-hosted-runner" in root_readme


def test_legal_brief_pipeline_splits_compile_and_publish_with_pinned_tectonic() -> None:
    wf = load_workflow("legal-brief-pipeline.yml")
    jobs = wf["jobs"]
    assert set(jobs) == {"compile-pdf", "publish-brief"}

    compile_job = jobs["compile-pdf"]
    publish_job = jobs["publish-brief"]

    assert effective_job_permissions(wf, compile_job) == {"contents": "read"}
    assert publish_job.get("needs") in ("compile-pdf", ["compile-pdf"])

    compile_dump = dump_yaml(compile_job)
    assert "secrets." not in compile_dump
    assert "drop.yannick.io" not in compile_dump
    assert TECTONIC_RELEASE_URL in compile_dump
    assert TECTONIC_SHA256 in compile_dump
    assert "sha256sum --check" in compile_dump

    compile_steps = job_step_names(compile_job)
    assert compile_steps.index("Install poppler-utils") < compile_steps.index(
        "Compile LaTeX and stage bounded brief bundle"
    )

    publish_dump = dump_yaml(publish_job)
    assert "duckdb==1.2.1" in publish_dump
    assert "scripts/legal_brief_publish.py" in publish_dump
    assert "curl " not in publish_dump


def test_global_workflow_and_dockerfile_security_invariants() -> None:
    for wf_path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        raw = wf_path.read_text(encoding="utf-8")
        assert not PIPE_TO_SHELL.search(raw), (
            f"{wf_path.name} pipes remote content to a shell"
        )
        assert "persist-credentials: true" not in raw, (
            f"{wf_path.name} persists git credentials on disk"
        )

    for docker_path in sorted(ROOT.rglob("Dockerfile*")):
        raw = docker_path.read_text(encoding="utf-8")
        assert not PIPE_TO_SHELL.search(raw), (
            f"{docker_path} pipes remote content to a shell"
        )

    for wf_name, job_name in WORKLOAD_EXECUTION_JOBS:
        wf = load_workflow(wf_name)
        job = wf["jobs"][job_name]
        perms = effective_job_permissions(wf, job)
        assert perms.get("id-token") != "write", (
            f"{wf_name}:{job_name} holds id-token: write"
        )
        dump = dump_yaml(job)
        assert "GH_PAT" not in dump, f"{wf_name}:{job_name} exposes GH_PAT"
        for step in job.get("steps", []):
            if isinstance(step, dict) and str(step.get("uses", "")).startswith(
                "actions/checkout@"
            ):
                assert step.get("with", {}).get("persist-credentials") is False, (
                    f"{wf_name}:{job_name} checkout must set persist-credentials: false"
                )

    for wf_name in TOUCHED_WORKFLOWS:
        wf = load_workflow(wf_name)
        for job_name, job in wf.get("jobs", {}).items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps", []):
                if not isinstance(step, dict) or "uses" not in step:
                    continue
                uses = str(step["uses"]).strip()
                if uses.startswith("./"):
                    continue
                assert EXTERNAL_ACTION_PIN.fullmatch(uses), (
                    f"{wf_name}:{job_name} uses unpinned action {uses!r}"
                )


def test_jefs_docket_adapter_stages_without_token_and_publishes_post_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domains.legal.adapters import jefs_docket_acquire as jefs

    workspace = tmp_path / "workload"
    (workspace / "TOOLS").mkdir(parents=True)
    (workspace / "TOOLS" / "jefs_docket_extractor.js").write_text(
        "// legacy marker\n", encoding="utf-8"
    )
    (workspace / "TOOLS" / "jefs_docket_extractor_ci.js").write_text(
        "// ci extractor\n", encoding="utf-8"
    )
    stage_dir = tmp_path / "jefs-stage"
    result_path = tmp_path / "results" / "JefsSyncJob01.json"
    result_path.parent.mkdir(parents=True)

    plan = {
        "job_id": "JefsSyncJob01",
        "pillar": "G",
        "action": "docket-sync",
        "adapter": "jefs-docket-acquire",
        "task": "acquire",
        "source_repo": "GlacierEQ/DOCKETS",
        "source_ref": "a" * 40,
        "target_repo": "GlacierEQ/DOCKETS",
    }

    monkeypatch.delenv("APEX_EVIDENCE_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("APEX_RESOLVED_SOURCE_SHA", "a" * 40)
    monkeypatch.setattr(jefs, "_find_chrome", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(jefs.shutil, "which", lambda _name: "/usr/bin/fake")

    class FakeProc:
        def __init__(self, returncode: int = 0, stdout: str = "ok\n") -> None:
            self.returncode = returncode
            self.stdout = stdout

    def fake_subprocess_run(cmd: list[str], **kwargs: Any) -> FakeProc:
        env = kwargs.get("env") or {}
        assert "APEX_EVIDENCE_WRITE_TOKEN" not in env
        if cmd[0] == "node":
            out_dir = Path(env["JEFS_OUTPUT_DIR"])
            pdf_bytes = b"%PDF-1.4 official seq 225\n"
            (out_dir / "seq_225.pdf").write_bytes(pdf_bytes)
            (out_dir / "docket_rows_223_226.json").write_text(
                '[{"seq": 225}]\n', encoding="utf-8"
            )
            (out_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "downloads": [
                            {
                                "seqNo": 225,
                                "status": 200,
                                "contentType": "application/pdf",
                                "isPdf": True,
                                "bytes": len(pdf_bytes),
                                "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                                "filename": "seq_225.pdf",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
        return FakeProc(0)

    monkeypatch.setattr(jefs.subprocess, "run", fake_subprocess_run)

    exit_code = jefs.run(plan, workspace, result_path, stage_dir=stage_dir)
    assert exit_code == 0
    staged_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert staged_result["status"] == "completed"
    assert staged_result["evidence_commits"] == []
    assert staged_result["required_gate"]["private_write_succeeded"] is False
    assert (stage_dir / "seq_225.pdf").is_file()
    assert (stage_dir / "ACQUISITION_RECEIPT.json").is_file()

    uploaded: list[tuple[str, bytes]] = []

    def fake_put(token: str, repo_path: str, data: bytes, message: str) -> str:
        assert token == "post-exec-write-token"
        assert message
        uploaded.append((repo_path, data))
        return "b" * 40

    monkeypatch.setenv("APEX_EVIDENCE_WRITE_TOKEN", "post-exec-write-token")
    monkeypatch.setattr(jefs, "_github_put", fake_put)

    pub_code = jefs.publish_staged_evidence(plan, stage_dir, result_path)
    assert pub_code == 0
    final_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert final_result["status"] == "completed"
    assert final_result["required_gate"]["private_write_succeeded"] is True
    assert len(final_result["evidence_commits"]) == 3
    assert len(uploaded) == 3


def test_mega_pdf_relay_publisher_uses_git_data_api_without_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from domains.code.adapters import mega_pdf_function_genome as genome
    import mega_pdf_relay_publish as relay_pub

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    receipt_payload = {
        "connector": "MEGA-PDF",
        "function": "pkg/mod.py::fn",
        "success": True,
        "started_at": "2026-08-09T00:00:00+00:00",
        "completed_at": "2026-08-09T00:00:00+00:00",
        "latency_ms": 0,
        "result_digest": "1" * 64,
        "previous_receipt_hash": None,
        "metadata": {"probe_level": "static"},
    }
    receipt_hash = hashlib.sha256(
        json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt_row = receipt_payload | {"receipt_hash": receipt_hash}
    report = {
        "schema_version": genome.EXPECTED_SCHEMA,
        "inventory_digest": "2" * 64,
        "receipt_root": receipt_hash,
        "receipt_chain_valid": True,
        "parse_failures": [],
        "scan_issues": [],
        "summary": {
            "discovered": 1,
            "promoted_to_probed": 1,
            "blocked": 0,
            "approved": 0,
            "defaults_promoted": 0,
            "receipts": 1,
        },
    }
    (artifacts / "function-genome.json").write_text(
        json.dumps(report) + "\n", encoding="utf-8"
    )
    (artifacts / "probe-receipts.jsonl").write_text(
        json.dumps(receipt_row, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (artifacts / "function-genome-summary.md").write_text(
        "# Summary\n", encoding="utf-8"
    )

    calls: list[tuple[str, str]] = []
    ref_reads = 0

    def fake_request(
        method: str, path: str, token: str, payload: dict | None = None
    ) -> dict:
        nonlocal ref_reads
        assert token == "mega-write-token"
        calls.append((method, path))
        if method == "GET" and path == "/git/ref/heads/automation/mega-pdf-function-genome-results":
            ref_reads += 1
            return {"object": {"sha": "a" * 40 if ref_reads == 1 else "c" * 40}}
        if method == "GET" and path == f"/git/commits/{'a' * 40}":
            return {"tree": {"sha": "d" * 40}}
        if method == "POST" and path == "/git/trees":
            assert payload is not None
            assert [entry["path"] for entry in payload["tree"]] == list(genome.OUTPUTS)
            return {"sha": "e" * 40}
        if method == "POST" and path == "/git/commits":
            return {"sha": "c" * 40}
        if method == "PATCH" and path == "/git/refs/heads/automation/mega-pdf-function-genome-results":
            return {"object": {"sha": "c" * 40}}
        raise AssertionError(f"unexpected API call: {method} {path}")

    monkeypatch.setenv("GITHUB_INSTALLATION_TOKEN", "mega-write-token")
    monkeypatch.setattr(relay_pub, "request", fake_request)

    published = relay_pub.publish_verified_artifacts(
        output_root=tmp_path,
        source_ref="main",
        source_sha="f" * 40,
        run_id="101",
        run_attempt="1",
    )
    assert published["published_sha"] == "c" * 40
    assert (artifacts / "relay-receipt.json").is_file()
    assert ref_reads == 2


def test_legal_brief_publisher_serializes_json_without_shell_interpolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import legal_brief_publish as brief_pub

    staging = tmp_path / "staging"
    staging.mkdir()
    pdf_bytes = b"%PDF-1.4 brief\n"
    (staging / "brief.pdf").write_bytes(pdf_bytes)
    tricky_case = 'Motion "To Dismiss" \n $(whoami)'
    metadata = {
        "pdf_name": "motion.pdf",
        "word_count": 420,
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "pdf_bytes": len(pdf_bytes),
        "case_name": tricky_case,
    }
    (staging / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    requests_seen: list[tuple[str, str, bytes | None]] = []

    def fake_http(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, bytes]:
        requests_seen.append((method, url, body))
        if "storage/v1/object" in url:
            return 200, b'{"Key":"ok"}'
        if url.endswith("/query"):
            return 200, b'{"results":[]}'
        if url.endswith("/v1/pages"):
            assert body is not None
            parsed = json.loads(body.decode("utf-8"))
            title = parsed["properties"]["Name"]["title"][0]["text"]["content"]
            assert title == tricky_case
            return 200, b'{"id":"page-1"}'
        raise AssertionError(f"unexpected URL: {url}")

    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "sb-secret")
    monkeypatch.setenv("NOTION_TOKEN", "notion-secret")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-123")
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "GlacierEQ/public-actions-runner-host")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_SHA", "1" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setattr(brief_pub, "_http_request", fake_http)

    assert brief_pub.publish_brief(staging) == 0
    assert len(requests_seen) == 3
    assert "motion.pdf" in summary_file.read_text(encoding="utf-8")
