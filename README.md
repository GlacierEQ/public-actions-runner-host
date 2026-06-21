# public-actions-runner-host

Public GitHub-hosted execution plane for the private APEX/GlacierEQ control system.

The private `GlacierEQ/llm-runner-teams` repository defines policy and nine specialized pillars. This public repository performs allowlisted legwork on `ubuntu-latest`, then returns detailed results to the private control plane.

## Pillar runner

| Pillar | Domain | Event |
|---|---|---|
| A | Case and Evidence | `case-evidence` |
| B | Document Processing | `document-processing` |
| C | Coding and Deploy | `coding-deploy` |
| D | Evolution and Optimization | `evolution-optimize` |
| E | Memory and Intelligence | `memory-sync` |
| F | Infrastructure and Gateway | `infra-gateway` |
| G | Federal Case Operations | `case-ops` |
| H | Orchestration and Swarm | `orchestrate` |
| I | International Case Operations | `intl-case-ops` |

Workflow: [`.github/workflows/apex-pillar-runner.yml`](.github/workflows/apex-pillar-runner.yml)

## Dispatch example

```json
{
  "event_type": "coding-deploy",
  "client_payload": {
    "job_id": "code-20260620-001",
    "pillar": "C",
    "source_repo": "GlacierEQ/Omni_Engine",
    "source_ref": "main",
    "task": "test"
  }
}
```

Dispatch payloads contain metadata only. Never include evidence, legal narratives, credentials, private source text, prompts, email content, or document contents.

Detailed results are written to `results/{job_id}.json` in the private control repository. Public workflow artifacts are not used.

Pillars G and I require a matching private dual-confirmation record.

See [Public Runner Security Contract](docs/PUBLIC_RUNNER_SECURITY.md) and [job schema](schemas/apex-public-job.schema.json).

## Required secrets

| Secret | Purpose |
|---|---|
| `APEX_CONTROL_TOKEN` | Read approvals and write results in the private control repository |
| `APEX_PRIVATE_READ_TOKEN` | Read approved private workload repositories |

Both should be fine-grained, repository-scoped tokens with minimum Contents permissions.

## Legal brief pipeline

The existing [legal brief workflow](.github/workflows/legal-brief-pipeline.yml) remains available for LaTeX compilation, Supabase upload, Notion synchronization, and MotherDuck metrics.
