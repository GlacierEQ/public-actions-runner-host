# APEX Public Runner Security Contract

This public repository provides GitHub-hosted execution capacity for the private APEX control plane.

## Trust boundary

- The public workflow receives only an opaque job ID, pillar, repository/ref, allowlisted task, and optional approval ID.
- No evidence, legal narrative, credentials, prompts, email content, or private document content belongs in a dispatch payload.
- Results are written to the private `GlacierEQ/llm-runner-teams` repository. They are never uploaded as public workflow artifacts.
- Workload credentials are not persisted in the checkout.
- Commands are selected by the checked-in runner adapter. Arbitrary shell commands are rejected.
- Pillars G and I require a matching private approval record before execution.

## Required repository secrets

| Secret | Scope |
|---|---|
| `APEX_CONTROL_TOKEN` | Fine-grained Contents read/write on `GlacierEQ/llm-runner-teams` |
| `APEX_PRIVATE_READ_TOKEN` | Fine-grained Contents read on approved private workload repositories |

Use separate tokens. Grant no administration, Actions-write, secrets, or organization-management permissions.

## Private control-plane paths

- `approvals/{approval_id}.json`: dual-confirmation records for pillars G and I.
- `results/{job_id}.json`: private result records returned by the public runner.

Example approval record:

```json
{
  "job_id": "case-20260620-001",
  "pillar": "G",
  "approved": true,
  "approved_by": ["operator", "reviewer"],
  "approved_at": "2026-06-20T00:00:00Z"
}
```

## Supported tasks

| Pillar | Tasks |
|---|---|
| A | `validate`, `hash-manifest` |
| B | `validate`, `test`, `build` |
| C | `validate`, `test`, `build`, `audit` |
| D | `validate`, `test`, `audit` |
| E | `validate`, `hash-manifest` |
| F | `validate`, `test`, `audit` |
| G | `validate`, `hash-manifest` plus private approval |
| H | `validate`, `test` |
| I | `validate`, `hash-manifest` plus private approval |

Public logs report status only. Detailed command output, file names, and manifests are returned to the private control plane.
