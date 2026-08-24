#!/usr/bin/env python3
"""Reject the retired GitHub-issue machine-job transport.

APEX machine jobs now enter through repository_dispatch, workflow_dispatch, or
repository-native jobs/<job_id>.json envelopes. Keeping this boundary as an
explicit rejection lets the existing workflow close legacy issue submissions
without executing them, while avoiding a broad workflow rewrite.
"""
from __future__ import annotations

import apex_pillar_runner as base


def main() -> int:
    base.fail(
        "GitHub issue transport for [APEX JOB] records is retired; use "
        "repository_dispatch, workflow_dispatch, or jobs/<job_id>.json"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
