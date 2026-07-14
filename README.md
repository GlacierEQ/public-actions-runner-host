# APEX Public Action Face

`GlacierEQ/public-actions-runner-host` is the **only GitHub Actions execution face** for the APEX/GlacierEQ runner system.

```text
external dispatcher / public issue / public repository_dispatch
  -> public-actions-runner-host
  -> GitHub-hosted ubuntu-latest
  -> approved workload checkout
  -> allowlisted adapter
  -> sanitized public status
  -> detailed private result in llm-runner-teams
```

## Canonical split

| Plane | Repository | Responsibility | GitHub Actions |
|---|---|---|---|
| Public execution / action face | `GlacierEQ/public-actions-runner-host` | Workflows, runs, badges, sanitized status, allowlisted execution | Required |
| Private control / runner teams | `GlacierEQ/llm-runner-teams` | Policy, nine pillars, approvals, private receipts, result history | Forbidden |
| Canonical architecture | `GlacierEQ/AKOS` | Governing policy and routing truth | Policy only |

The action-face guard fails closed unless GitHub reports this repository as **public**. Changing this repository to private blocks execution by design.

## Supported lanes

| Pillar | Domain | Primary event |
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

Migrated execution events also include `media-queue`, `whisperx-exec`, `gateway-ci`, `comet-agent-ci`, and `apex-verification`.

## Public issue queue

Open an issue titled `[APEX JOB] <job_id>` with a metadata-only JSON body:

```json
{
  "job_id": "code-20260714-001",
  "pillar": "C",
  "action": "apex-verification",
  "source_ref": "main"
}
```

Never include evidence, legal narratives, source text, prompts, messages, credentials, document contents, or private logs in a public issue.

## Private bridge

| Secret | Minimum purpose |
|---|---|
| `APEX_PRIVATE_READ_TOKEN` | Read approved private workload repositories |
| `APEX_CONTROL_TOKEN` | Read approvals and write detailed results in `GlacierEQ/llm-runner-teams` |
| `GH_PAT` | Approved minimum-scope fallback only |

Private checkout uses `persist-credentials: false`. Detailed results are never posted publicly. Pillars G and I require a matching private approval record.

## Core files

- [Action Face Contract](docs/ACTION_FACE_CONTRACT.md)
- [Public Runner Security Contract](docs/PUBLIC_RUNNER_SECURITY.md)
- [Action Catalog](config/pillar-actions.json)
- [Migrated Action Catalog](config/action-face-actions.json)
- [Canonical Workflow](.github/workflows/apex-pillar-runner.yml)
- [Visibility Guard](scripts/action_face_guard.py)

The existing legal brief workflow remains a public execution lane. It does not transfer control-plane ownership into a private repository.
