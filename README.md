# APEX Public Action Face

`GlacierEQ/public-actions-runner-host` is the **sole GitHub Actions execution face** for the APEX/GlacierEQ system.

```text
authorized external ingress
  -> immutable public repository identity check
  -> strict metadata-only envelope
  -> private control-plane invariant check
  -> duplicate-job replay guard
  -> ephemeral catalog-approved workload checkout
  -> isolated allowlisted adapter on ubuntu-latest
  -> immutable detailed private receipt
  -> truthful sanitized public status
```

## Start the bridge

On Windows, clone or download this repository and double-click:

```text
START_APEX_RUNNER_BRIDGE.cmd
```

That launcher checks GitHub CLI and Python, opens browser authentication only when necessary, and runs the hardened GitHub App Manifest bootstrap. The owner may need to approve GitHub's account-consent and selected-repository installation screens, but does **not** generate, view, download, copy, paste, store, or transmit a private key.

The cross-platform command behind the launcher is:

```bash
python github-app/bootstrap_apex_github_app.py
```

The bootstrap creates the owner-only App, receives the generated Client ID and PEM in process memory, writes the repository variable and encrypted secret, enforces the exact installation allowlist, reruns the existing failed workflow, and requires every completion-contract step to succeed. No PAT fallback exists.

See [Automated Owner Bootstrap](github-app/ACTIVATION.md).

## Canonical split

| Plane | Repository | Responsibility | GitHub Actions |
|---|---|---|---|
| Public execution / action face | `GlacierEQ/public-actions-runner-host` | Workflows, runs, badges, sanitized status, allowlisted execution | Sole owner |
| Private control / runner teams | `GlacierEQ/llm-runner-teams` | Policy, pillars, approvals, append-only private receipts | Forbidden |
| Canonical architecture | `GlacierEQ/AKOS` | Governing policy and routing truth | Policy only |

## Fail-closed identity

The public runner binds:

- repository full name;
- immutable repository ID;
- owner login and numeric owner ID;
- public visibility;
- `main` as default branch;
- non-archived, non-disabled, non-fork state.

A mismatch blocks execution before workload checkout.

## Authorized ingress

Execution ingress is limited to the repository owner identity recorded in `config/authorized-actors.json`.

Supported routes:

- owner-created `[APEX JOB] <job_id>` public issue;
- owner `workflow_dispatch`;
- owner-authenticated `repository_dispatch`;
- owner push of one bounded `jobs/<job_id>.json` envelope.

Public issue author association, actor login, event role, and GitHub numeric actor ID are validated. Anonymous or unauthorized public issue authors are never execution principals.

## Strict job envelope

The envelope permits only:

```text
job_id
pillar
action
source_repo
source_ref
task
approval_id
```

Unknown fields, control characters, conflicting pillar declarations, oversized payloads, path traversal, arbitrary repositories, and catalog-action overrides are rejected before checkout.

Catalog actions choose their own approved repository and adapter. Base tasks may target only repositories already present in the catalog-derived allowlist.

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

Additional execution events:

```text
media-queue
whisperx-exec
gateway-ci
comet-agent-ci
apex-verification
action-face-canary
```

## Dedicated GitHub App bridge

No broad PAT fallback is allowed. The only stored bridge identity is:

| Name | Kind | Purpose | Workload exposure |
|---|---|---|---|
| `APEX_RUNNER_APP_CLIENT_ID` | Repository variable | Identifies the owner-only APEX Runner Bridge App | Token-minting configuration only |
| `APEX_RUNNER_APP_PRIVATE_KEY` | Repository secret | Mints short-lived installation tokens | Never exported to workload processes |

The stored identity is created and written by the automated manifest bootstrap. It is not manually transported.

At runtime the workflow mints two separate, short-lived tokens:

| Runtime token | Scope | Permission |
|---|---|---|
| `APEX_CONTROL_TOKEN` | `GlacierEQ/llm-runner-teams` only | Contents read/write for claims, approvals, and immutable receipts |
| `APEX_PRIVATE_READ_TOKEN` | Exactly one catalog-approved workload repository | Contents read only |

Both checkout operations use immutable action revisions and `persist-credentials: false`. Runtime tokens are revoked automatically and stripped from the workload process environment.

See `config/required-secrets.json` and `github-app/bridge-contract.json` for the least-privilege contract.

## Private control-plane gate

Before planning a workload, the public face verifies that `GlacierEQ/llm-runner-teams`:

- remains private, enabled, non-forked, and on `main`;
- contains no executable private workflow YAML;
- has an active no-private-actions policy;
- points execution to this public repository;
- has an active append-only result policy;
- forbids result overwrite and deletion.

## Immutable result receipts

One job ID produces one private receipt:

```text
results/<job_id>.json
```

A duplicate job ID is blocked before workload checkout. Publishing an existing result path is forbidden.

Each receipt binds:

```text
payload SHA-256
publication timestamp
workflow run ID
workflow run attempt
public runner commit SHA
execution repository
trigger actor and actor ID
source repository and source ref
```

Successful workload execution without successful private publication remains a blocked release state.

## Canary before workload trust

Activation is two-stage:

1. `action-face-canary` verifies syntax, JSON contracts, schema alignment, immutable checkout pinning, secret isolation, workflow invariants, catalog uniqueness, strict planning, and authorization denial paths.
2. `apex-verification` runs the target quality/function/security/hardening suite only after the canary passes.

See [Canary Protocol](docs/CANARY_PROTOCOL.md).

## Public truth boundary

Public status contains only identifiers, lane, outcome, private-receipt state, and run URL. Evidence, legal narratives, source contents, prompts, messages, credentials, document contents, and detailed logs remain private.

## Core files

- [One-click Windows launcher](START_APEX_RUNNER_BRIDGE.cmd)
- [Automated Owner Bootstrap](github-app/ACTIVATION.md)
- [Bridge Contract](github-app/bridge-contract.json)
- [Action Face Contract](docs/ACTION_FACE_CONTRACT.md)
- [Public Runner Security](docs/PUBLIC_RUNNER_SECURITY.md)
- [Canary Protocol](docs/CANARY_PROTOCOL.md)
- [Required Secrets Contract](config/required-secrets.json)
- [Immutable Identity](config/action-face-identity.json)
- [Authorized Actors](config/authorized-actors.json)
- [Strict Envelope Schema](config/job-envelope.schema.json)
- [Primary Action Catalog](config/pillar-actions.json)
- [Action-Face Catalog](config/action-face-actions.json)
- [Canonical Workflow](.github/workflows/apex-pillar-runner.yml)

## Current activation condition

Repository code, the no-manual-key bootstrap, the Windows launcher, and their verification tests are merged. The private bridge itself becomes active only after the launcher completes GitHub's consent flow and the rerun returns every required completion record as `success`.

Until that receipt exists, private execution is **not activated**. PR #63 and Monolith PR #3 remain open and unmerged.