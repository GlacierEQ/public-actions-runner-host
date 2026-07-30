# Anthropic Agent Coordinator — Public Runner Route

## Registered action

| Field | Value |
|---|---|
| Pillar | `H` — Orchestration and Swarm |
| Public event | `orchestrate` |
| Action | `anthropic-agent-coordinator-ci` |
| Fixed target | `GlacierEQ/anthropic-agent-coordinator` |
| Adapter | `python-ci` |
| Gate | `elevated` |
| Private control plane | `GlacierEQ/llm-runner-teams` |

The catalog fixes the workload repository and adapter. A dispatch may choose only the source ref; it cannot override the repository, task, or executable command.

## Metadata-only dispatch

```json
{
  "event_type": "orchestrate",
  "client_payload": {
    "job_id": "coordinator-ci-20260730-001",
    "action": "anthropic-agent-coordinator-ci",
    "source_ref": "wave-1/coordinator-promotion-rebased-2026-07-30"
  }
}
```

The same request may be submitted through an owner-created public issue titled:

```text
[APEX JOB] coordinator-ci-20260730-001
```

The issue body must contain the metadata-only job envelope itself, without the repository-dispatch wrapper:

```json
{
  "job_id": "coordinator-ci-20260730-001",
  "pillar": "H",
  "action": "anthropic-agent-coordinator-ci",
  "source_ref": "wave-1/coordinator-promotion-rebased-2026-07-30"
}
```

Do not include source code, prompts, credentials, test logs, or private evidence in the public payload.

## Execution boundary

The public action face:

1. validates the owner and immutable public-runner identity;
2. resolves the cataloged coordinator repository;
3. verifies the private non-executing control plane;
4. claims the job ID to prevent replay;
5. checks out the requested coordinator ref without persisted credentials;
6. runs the hardened `python-ci` adapter in a secret-stripped environment;
7. returns the detailed immutable result to `llm-runner-teams`;
8. publishes only sanitized public status.

## Repository-native matrix

The coordinator repository also calls:

```text
GlacierEQ/public-actions-runner-host/.github/workflows/reusable-quick-ci.yml@main
```

Its repository-owned `scripts/ci/verify.sh` preserves the stronger promotion contract: Python 3.11–3.13, Ruff, compilation, source and wheel builds, isolated wheel installation, README verification, CLI verification, JUnit reconciliation, SHA-256-bound receipts, and bounded artifact upload.

The action-face result and the repository-native matrix are complementary evidence. Neither substitutes for the other.
