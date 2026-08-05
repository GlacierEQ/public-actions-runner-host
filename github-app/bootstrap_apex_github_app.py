#!/usr/bin/env python3
"""Bootstrap the APEX GitHub App bridge without manual key handling."""

from __future__ import annotations

import argparse
import html
import json
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

TARGET_REPO: Final = "GlacierEQ/public-actions-runner-host"
CLIENT_ID_VARIABLE: Final = "APEX_RUNNER_APP_CLIENT_ID"
PRIVATE_KEY_SECRET: Final = "APEX_RUNNER_APP_PRIVATE_KEY"
DEFAULT_RUN_ID: Final = 30964992458
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
EXPECTED_REPOSITORIES: Final[frozenset[str]] = frozenset(
    {
        "GlacierEQ/mastermind",
        "GlacierEQ/llm-runner-teams",
        "GlacierEQ/monolith",
        "GlacierEQ/MEGA-PDF",
    }
)


class BootstrapError(RuntimeError):
    """Raised when the bootstrap cannot safely complete."""


def run_gh(
    args: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run GitHub CLI without echoing arguments or stdin."""
    completed = subprocess.run(
        ["gh", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown gh error"
        raise BootstrapError(f"GitHub CLI command failed: {detail}")
    return completed


def require_prerequisites() -> None:
    if shutil.which("gh") is None:
        raise BootstrapError("GitHub CLI (`gh`) is required but was not found.")
    status = run_gh(["auth", "status"], check=False)
    if status.returncode != 0:
        raise BootstrapError(
            "GitHub CLI is not authenticated. Use an authenticated desktop agent "
            "or complete `gh auth login --web` in the browser."
        )


def load_manifest(path: Path, redirect_url: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["redirect_url"] = redirect_url
    payload["public"] = False
    payload["default_events"] = []
    payload["default_permissions"] = {"contents": "write"}
    payload["request_oauth_on_install"] = False
    payload["setup_on_update"] = False

    hook_attributes = payload.setdefault("hook_attributes", {})
    hook_attributes["active"] = False
    hook_attributes.pop("url", None)

    if payload.get("name") != "APEX Runner Bridge":
        raise BootstrapError("Manifest app name must be exactly `APEX Runner Bridge`.")
    return payload


def registration_page(manifest: dict[str, Any], state: str) -> bytes:
    manifest_json = json.dumps(manifest, separators=(",", ":"))
    escaped = html.escape(manifest_json, quote=True)
    escaped_state = html.escape(state, quote=True)
    page = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>APEX Runner Bridge bootstrap</title></head>
<body>
  <h1>APEX Runner Bridge</h1>
  <p>GitHub will show the exact owner-only App configuration before creation.</p>
  <form id="manifest-form" action="https://github.com/settings/apps/new" method="post">
    <input type="hidden" name="manifest" value="{escaped}">
    <input type="hidden" name="state" value="{escaped_state}">
    <button type="submit">Create the GitHub App</button>
  </form>
  <script>document.getElementById("manifest-form").submit();</script>
</body>
</html>"""
    return page.encode("utf-8")


def callback_page(success: bool, message: str) -> bytes:
    title = "GitHub App registered" if success else "GitHub App registration failed"
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><body><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p></body></html>"
    ).encode("utf-8")


def receive_manifest_code(
    manifest: dict[str, Any],
    *,
    host: str,
    port: int,
    timeout_seconds: int,
) -> str:
    callback_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=1)
    expected_state = secrets.token_urlsafe(32)
    manifest["redirect_url"] = f"http://{host}:{port}/callback"
    start_page = registration_page(manifest, expected_state)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def send_html(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self.send_html(200, start_page)
                return
            if parsed.path != "/callback":
                self.send_html(404, callback_page(False, "Unknown bootstrap route."))
                return

            values = urllib.parse.parse_qs(parsed.query)
            code = values.get("code", [""])[0]
            returned_state = values.get("state", [""])[0]
            if not code:
                callback_queue.put(("error", "GitHub did not return a manifest code."))
                self.send_html(400, callback_page(False, "Missing manifest code."))
                return
            if returned_state != expected_state:
                callback_queue.put(("error", "Manifest state validation failed."))
                self.send_html(400, callback_page(False, "State validation failed."))
                return

            callback_queue.put(("ok", code))
            self.send_html(
                200,
                callback_page(
                    True,
                    "The local bootstrap process is securely finishing configuration.",
                ),
            )

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bootstrap_url = f"http://{host}:{port}/"
        if not webbrowser.open(bootstrap_url):
            print(f"Open this local bootstrap URL in the controlled browser: {bootstrap_url}")
        status, value = callback_queue.get(timeout=timeout_seconds)
        if status != "ok":
            raise BootstrapError(value)
        return value
    except queue.Empty as exc:
        raise BootstrapError("Timed out waiting for GitHub App registration.") from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def exchange_manifest_code(code: str) -> dict[str, Any]:
    result = run_gh(
        ["api", "--method", "POST", f"/app-manifests/{code}/conversions"]
    )
    payload = json.loads(result.stdout)
    required = ("client_id", "pem", "slug")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise BootstrapError(
            "GitHub manifest conversion omitted required fields: " + ", ".join(missing)
        )
    pem = str(payload["pem"])
    if "BEGIN " not in pem or "PRIVATE KEY" not in pem:
        raise BootstrapError("GitHub returned an invalid private-key payload.")
    return payload


def write_repository_settings(client_id: str, pem: str) -> None:
    run_gh(
        [
            "variable",
            "set",
            CLIENT_ID_VARIABLE,
            "--repo",
            TARGET_REPO,
            "--body",
            client_id,
        ]
    )
    run_gh(
        [
            "secret",
            "set",
            PRIVATE_KEY_SECRET,
            "--repo",
            TARGET_REPO,
            "--body",
            pem,
        ]
    )


def list_installation_repositories(app_slug: str) -> frozenset[str] | None:
    installations = json.loads(run_gh(["api", "/user/installations"]).stdout)
    candidates = [
        item
        for item in installations.get("installations", [])
        if item.get("app_slug") == app_slug
    ]
    if not candidates:
        return None

    repository_names: set[str] = set()
    for installation in candidates:
        installation_id = installation.get("id")
        if not installation_id:
            continue
        payload = json.loads(
            run_gh(
                [
                    "api",
                    "--paginate",
                    "--slurp",
                    f"/user/installations/{installation_id}/repositories",
                ]
            ).stdout
        )
        pages = payload if isinstance(payload, list) else [payload]
        for page in pages:
            for repository in page.get("repositories", []):
                full_name = repository.get("full_name")
                if full_name:
                    repository_names.add(str(full_name))
    return frozenset(repository_names)


def wait_for_exact_installation(
    app_slug: str,
    *,
    timeout_seconds: int,
    poll_seconds: int = 5,
) -> None:
    installation_url = f"https://github.com/apps/{app_slug}/installations/new"
    print(
        "The App was created and both repository settings were written. "
        "The controlled browser is opening the installation screen."
    )
    webbrowser.open(installation_url)
    print("Required installation allowlist:")
    for repository in sorted(EXPECTED_REPOSITORIES):
        print(f"  - {repository}")

    deadline = time.monotonic() + timeout_seconds
    last_observed: frozenset[str] | None = None
    while time.monotonic() < deadline:
        observed = list_installation_repositories(app_slug)
        last_observed = observed
        if observed == EXPECTED_REPOSITORIES:
            return
        if observed is not None and not observed.issubset(EXPECTED_REPOSITORIES):
            extras = sorted(observed - EXPECTED_REPOSITORIES)
            raise BootstrapError(
                "The App installation includes unauthorized repositories: "
                + ", ".join(extras)
            )
        time.sleep(poll_seconds)

    if last_observed is None:
        detail = "no installation was observed"
    else:
        missing = sorted(EXPECTED_REPOSITORIES - last_observed)
        detail = "missing repositories: " + ", ".join(missing)
    raise BootstrapError(f"Timed out waiting for the exact installation; {detail}.")


def rerun_and_report(run_id: int, *, timeout_seconds: int) -> int:
    run_gh(["run", "rerun", str(run_id), "--failed", "--repo", TARGET_REPO])
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}

    while time.monotonic() < deadline:
        view = run_gh(
            [
                "run",
                "view",
                str(run_id),
                "--repo",
                TARGET_REPO,
                "--json",
                "status,conclusion,jobs,url",
            ]
        )
        latest = json.loads(view.stdout)
        if latest.get("status") == "completed":
            break
        time.sleep(8)
    else:
        raise BootstrapError("Timed out waiting for the workflow rerun.")

    print(f"Workflow: {latest.get('url', '')}")
    print(f"Conclusion: {latest.get('conclusion') or 'unknown'}")
    for job in latest.get("jobs", []):
        print(f"Job {job.get('name')}: {job.get('conclusion') or job.get('status')}")
        for step in job.get("steps", []):
            print(
                f"  {step.get('name')}: "
                f"{step.get('conclusion') or step.get('status')}"
            )

    return 0 if latest.get("conclusion") == "success" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and activate the APEX Runner Bridge without manual key handling."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("app-manifest.json"),
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--run-id", type=int, default=DEFAULT_RUN_ID)
    parser.add_argument("--registration-timeout", type=int, default=900)
    parser.add_argument("--installation-timeout", type=int, default=900)
    parser.add_argument("--workflow-timeout", type=int, default=1200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        require_prerequisites()
        redirect_url = f"http://{args.host}:{args.port}/callback"
        manifest = load_manifest(args.manifest, redirect_url)
        code = receive_manifest_code(
            manifest,
            host=args.host,
            port=args.port,
            timeout_seconds=args.registration_timeout,
        )
        app = exchange_manifest_code(code)
        client_id = str(app["client_id"])
        pem = str(app["pem"])
        app_slug = str(app["slug"])

        write_repository_settings(client_id, pem)
        del pem
        app.pop("pem", None)
        app.pop("client_secret", None)
        app.pop("webhook_secret", None)

        wait_for_exact_installation(
            app_slug,
            timeout_seconds=args.installation_timeout,
        )
        return rerun_and_report(args.run_id, timeout_seconds=args.workflow_timeout)
    except (BootstrapError, json.JSONDecodeError, OSError) as exc:
        print(f"BOOTSTRAP_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
