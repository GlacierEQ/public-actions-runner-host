import json
import os
import urllib.request
from pathlib import Path

FAILURES = []
COUNTS = {"passed": 0, "failed": 0, "skipped": 0}

def _record(report):
    if getattr(report, "passed", False): COUNTS["passed"] += 1
    elif getattr(report, "skipped", False): COUNTS["skipped"] += 1
    elif getattr(report, "failed", False):
        COUNTS["failed"] += 1
        longrepr = getattr(report, "longrepr", None)
        crash = getattr(longrepr, "reprcrash", None)
        msg = str(getattr(crash, "message", "")) if crash else str(longrepr)
        FAILURES.append({"nodeid": str(getattr(report, "nodeid", "collection")), "message": msg[-1600:]})
        print(f"::error::{FAILURES[-1]['nodeid']}: {FAILURES[-1]['message']}")

def pytest_runtest_logreport(report):
    if report.when == "call": _record(report)

def pytest_collectreport(report):
    if report.failed: _record(report)

def pytest_sessionfinish(session, exitstatus):
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    token = os.environ.get("GH_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not event_path or not token or "/" not in repository: return
    try:
        issue_number = int(json.loads(Path(event_path).read_text())["issue"]["number"])
        payload = {"pytest_exit_status": int(exitstatus), "counts": COUNTS, "failures": FAILURES[:30]}
        body = "## AKOS diagnostic pytest evidence\n\n```json\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n```"
        req = urllib.request.Request(f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments", data=json.dumps({"body": body}).encode(), method="POST", headers={"Accept":"application/vnd.github+json","Authorization":f"Bearer {token}","Content-Type":"application/json","User-Agent":"akos-diagnostic","X-GitHub-Api-Version":"2022-11-28"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:
        print(f"AKOS_REPORTER_WARNING: {type(exc).__name__}: {exc}")
