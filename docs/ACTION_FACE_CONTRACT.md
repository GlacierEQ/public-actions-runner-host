# APEX Public Action Face Contract

## Canonical roles

| Role | Repository | Visibility | GitHub Actions |
|---|---|---:|---:|
| Public action face / execution plane | `GlacierEQ/public-actions-runner-host` | **Public required** | **Allowed and required** |
| Private control brain / policy and receipts | `GlacierEQ/llm-runner-teams` | Private | **Forbidden** |
| Private workload repositories | Approved `GlacierEQ/*` repositories | Private or public | Forbidden when private |
| Canonical architecture | `GlacierEQ/AKOS` | Private | Policy only |

## Prime rule

> Every GitHub-hosted execution run, workflow badge, sanitized public status, and runner-facing event belongs to `GlacierEQ/public-actions-runner-host`.

The private control plane may define policy, approvals, job envelopes, and immutable result records. It must not own executable workflow YAML.

## Execution path

```text
external dispatcher / public issue / public repository_dispatch
  -> public-actions-runner-host
  -> action-face visibility guard
  -> metadata-only plan validation
  -> short-lived checkout of approved workload
  -> allowlisted adapter on ubuntu-latest
  -> sanitized public status
  -> detailed private receipt in llm-runner-teams
```

## Allowed ingress

- Public issue queue with metadata-only JSON.
- `workflow_dispatch` on the public action face.
- `repository_dispatch` addressed to the public action face.
- Public source repositories may dispatch to the public action face.

Private source repositories must not create their own GitHub Actions workflow merely to dispatch. Use an external connector/operator or the public issue queue.

## Allowed events

- `case-evidence`
- `document-processing`
- `coding-deploy`
- `evolution-optimize`
- `memory-sync`
- `infra-gateway`
- `case-ops`
- `orchestrate`
- `intl-case-ops`
- `media-queue`
- `whisperx-exec`
- `gateway-ci`
- `comet-agent-ci`
- `apex-verification`

## Security boundaries

1. Public payloads contain identifiers and routing metadata only.
2. Evidence, document contents, prompts, messages, credentials, and private logs never enter public issues or public artifacts.
3. Private checkout uses `APEX_PRIVATE_READ_TOKEN` or the minimum-scope approved fallback, with `persist-credentials: false`.
4. Detailed results return only to `GlacierEQ/llm-runner-teams` through `APEX_CONTROL_TOKEN`.
5. Pillars G and I require a matching private dual-confirmation record.
6. The action-face guard blocks execution unless the workflow repository is exactly `GlacierEQ/public-actions-runner-host` and GitHub reports its visibility as `public`.
7. No `workflow_call` bridge may make a private repository the billed or visible execution owner.

## Drift conditions that block release

- The action-face repository is private.
- Any executable `.yml` or `.yaml` workflow exists in `GlacierEQ/llm-runner-teams`.
- A private workload repository runs GitHub Actions directly.
- Detailed results are written to a public issue or public artifact.
- A public job envelope contains protected content instead of metadata.

## Result ownership

The public action face owns execution status. The private control plane owns detailed truth, approvals, and receipts. Neither repository may silently assume the other's role.
