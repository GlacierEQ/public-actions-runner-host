# APEX Public Action Face

`GlacierEQ/public-actions-runner-host` is the **sole GitHub Actions execution face** for the APEX/GlacierEQ system.

```text
authorized external ingress
  -> immutable public repository identity check
  -> strict metadata-only envelope
  -> GitHub Actions OIDC identity
  -> Keymaster broker
  -> short-lived one-repository installation tokens
  -> private control-plane invariant check
  -> duplicate-job replay guard
  -> ephemeral catalog-approved workload checkout
  -> isolated allowlisted adapter on ubuntu-latest
  -> immutable detailed private receipt
  -> explicit installation-token revocation
  -> truthful sanitized public status
```

## Start the canonical bridge

The canonical `APEX Public Action Face` does **not** require a repository-stored GitHub App Client ID, private key, PAT, HMAC secret, or PEM handoff.

The workflow requests a GitHub Actions OIDC identity (`id-token: write`) and exchanges it with the Keymaster broker. Keymaster validates the exact repository/workflow/actor claims, resolves the centrally managed GitHub App identity behind Vault references, and mints only the short-lived one-repository token required for the current operation. The public runner never receives the App private key.

The older launcher and GitHub App Manifest bootstrap remain in this repository only for the dedicated legacy App-bridge canary/recovery lane:

```text
START_APEX_RUNNER_BRIDGE.cmd
github-app/start_apex_runner_bridge.ps1
python github-app/bootstrap_apex_github_app.py
.github/workflows/apex-github-app-bridge-canary.yml
```

That legacy lane still preserves the original no-manual-key property, but its `APEX_RUNNER_APP_CLIENT_ID` and `APEX_RUNNER_APP_PRIVATE_KEY` configuration is **not consumed by the canonical APEX Public Action Face**. See [Automated Owner Bootstrap](github-app/ACTIVATION.md) only when working on that compatibility canary or recovery path.

## Canonical split

| Plane | Repository | Responsibility | GitHub Actions |
|---|---|---|---|
| Public execution / action face | `GlacierEQ/public-actions-runner-host` | Workflows, runs, badges, sanitized status, allowlisted execution | Sole owner |
| Private control / runner teams | `GlacierEQ/llm-runner-teams` | Policy, pillars, approvals, append-only private receipts | Forbidden |
| Canonical architecture | `GlacierEQ/AKOS` | Governing policy and routing truth | Policy only |

## Fail-closed identity

The public runner binds:

- repository full name and immutable repository ID;
- owner login and numeric owner ID;
- public visibility;
- exact canonical workflow identity;
- GitHub actor identity;
- accepted event/ref boundary;
- `main` as the canonical execution branch.

A mismatch blocks token minting and workload checkout.

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

## Keymaster OIDC credential bridge

No broad PAT fallback is allowed. The canonical workflow has **zero GitHub App credentials stored for its own token-mint path**.

```text
GitHub-hosted runner
  -> GitHub OIDC token
  -> exact-identity Keymaster broker
  -> Vault-backed GitHub App identity
  -> one repository + minimum permissions
  -> short-lived installation token
  -> operation
  -> explicit DELETE /installation/token
```

At runtime the workflow mints two separate tokens:

| Runtime token | Scope | Permission |
|---|---|---|
| `APEX_CONTROL_TOKEN` | `GlacierEQ/llm-runner-teams` only | Contents read/write for claims, approvals, and immutable receipts |
| `APEX_PRIVATE_READ_TOKEN` | Exactly one catalog-approved workload repository | Contents read only |

Repository identity, permission, and operation are bound together by tests. Workload repository data is passed to the mint command through an environment variable rather than shell template interpolation. Both checkout operations use immutable action revisions and `persist-credentials: false`. Neither App private-key material nor runtime tokens are exposed to the workload process.

Both minted tokens are explicitly revoked after publication/status handling. A required revocation failure is part of the governed release failure condition.

See `config/required-secrets.json` for the canonical OIDC contract. `github-app/bridge-contract.json` now explicitly describes the **legacy App-bridge canary/recovery** path rather than the canonical runner.

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

- [Canonical workflow](.github/workflows/apex-pillar-runner.yml)
- `scripts/keymaster_oidc_token.py` — OIDC-to-Keymaster exchange
- `scripts/revoke_github_installation_token.py` — explicit token revocation
- [Required Secrets/Auth Contract](config/required-secrets.json)
- [Action Face Contract](docs/ACTION_FACE_CONTRACT.md)
- [Public Runner Security](docs/PUBLIC_RUNNER_SECURITY.md)
- [Canary Protocol](docs/CANARY_PROTOCOL.md)
- [Immutable Identity](config/action-face-identity.json)
- [Authorized Actors](config/authorized-actors.json)
- [Strict Envelope Schema](config/job-envelope.schema.json)
- [Primary Action Catalog](config/pillar-actions.json)
- [Action-Face Catalog](config/action-face-actions.json)
- [Legacy One-click Windows launcher](START_APEX_RUNNER_BRIDGE.cmd)
- [Legacy App-bridge Bootstrap](github-app/ACTIVATION.md)
- [Legacy App-bridge Contract](github-app/bridge-contract.json)

## Current activation condition

The canonical OIDC/Keymaster implementation is code-complete only when its repository CI and review gates are green; **operational activation additionally requires a real APEX Public Action Face run that successfully mints both narrow tokens through Keymaster, checks out the exact private workload revision, executes and publishes the governed result, revokes both tokens, and leaves the private receipt.**

Until that live receipt exists, do not describe the OIDC path as operationally complete.
