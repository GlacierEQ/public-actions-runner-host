# public-actions-runner-host

Public GitHub-hosted execution plane for the private APEX/GlacierEQ control system.

The private `GlacierEQ/llm-runner-teams` repository defines policy and nine specialized pillars. This public repository performs allowlisted legwork on `ubuntu-latest`.

## Pillars

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

## Zapier issue queue

Zapier opens an issue titled `[APEX JOB] <job_id>`. The issue body is metadata-only JSON:

```json
{
  "job_id": "code-20260620-001",
  "pillar": "C",
  "source_repo": "GlacierEQ/Omni_Engine",
  "source_ref": "main",
  "task": "test"
}
```

The public workflow validates the contract, executes only allowlisted operations, posts sanitized status to the issue, and closes successful jobs.

Never include evidence, legal narratives, credentials, source text, prompts, messages, email content, or document contents in the public issue.

## Private bridge

Private workload checkout and private result return use:

| Secret | Purpose |
|---|---|
| `APEX_PRIVATE_READ_TOKEN` | Contents-read access to approved private workload repositories |
| `APEX_CONTROL_TOKEN` | Read approvals and write detailed results in `GlacierEQ/llm-runner-teams` |
| `GH_PAT` | Supported fallback when already installed with both minimum scopes |

Without one of these private-access secrets, public-repository workloads run normally and private-repository jobs remain gated.

Pillars G and I additionally require a matching private approval record.

See [Public Runner Security Contract](docs/PUBLIC_RUNNER_SECURITY.md), [job schema](schemas/apex-public-job.schema.json), and [workflow](.github/workflows/apex-pillar-runner.yml).

## Legal brief pipeline

The existing [legal brief workflow](.github/workflows/legal-brief-pipeline.yml) remains available for LaTeX compilation, Supabase upload, Notion synchronization, and MotherDuck metrics.
