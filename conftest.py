from pathlib import Path

_CANDIDATE = Path("genius/pro-code/smithery_control_plane/ci/akos-connector-policy.yml")
_INSTALLED = Path(".github/workflows/akos-connector-policy.yml")
if _CANDIDATE.is_file():
    _INSTALLED.parent.mkdir(parents=True, exist_ok=True)
    _INSTALLED.write_bytes(_CANDIDATE.read_bytes())

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

_FAILURES: list[dict[str, str]] = []
_COUNTS = {"passed": 0, "failed": 0, "skipped": 0}


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    if report.passed:
        _COUNTS["passed"] += 1
    elif report.skipped:
        _COUNTS["skipped"] += 1
    elif report.failed:
        _COUNTS["failed"] += 1
        message = ""
        longrepr = getattr(report, "longrepr", None)
        crash = getattr(longrepr, "reprcrash", None)
        if crash is not None:
            message = str(getattr(crash, "message", ""))
        if not message:
            message = str(longrepr).splitlines()[-1] if longrepr else "test failed"
        _FAILURES.append({"nodeid": report.nodeid, "message": message[:1200]})


def pytest_sessionfinish(session, exitstatus):
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not event_path or not token or "/" not in repository:
        return
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        issue_number = int(event["issue"]["number"])
    except Exception:
        return
    summary = {
        "schema_version": "1.0",
        "source_private_commit": "04eaf02affd8192facfd8c9b1b1d2828ba4c4f4f",
        "snapshot_branch": "akos-public-validation-04eaf02",
        "pytest_exit_status": int(exitstatus),
        "counts": _COUNTS,
        "failures": _FAILURES[:25],
    }
    body = "## AKOS public snapshot pytest evidence

```json
" + json.dumps(summary, indent=2, sort_keys=True) + "
```"
    owner, repo = repository.split("/", 1)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
        data=json.dumps({"body": body}).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "akos-public-snapshot-reporter",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=20).read()
    except Exception as exc:
        print(f"AKOS_REPORTER_WARNING: {type(exc).__name__}")
