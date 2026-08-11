# APEX Public Action Face Contract

**Status:** Active architecture; runtime activation blocked until public visibility and canary evidence exist.

## Canonical roles

| Role | Repository | Required visibility | GitHub Actions |
|---|---|---:|---:|
| Public action face / execution plane | `GlacierEQ/public-actions-runner-host` | Public | Sole execution owner |
| Private control brain / approvals / receipts | `GlacierEQ/llm-runner-teams` | Private | Forbidden |
| Private workload repositories | Catalog-approved `GlacierEQ/*` repositories | Private or public | Forbidden when private |
| Canonical architecture | `GlacierEQ/AKOS` | Private | Policy only |

## Prime rule

> Every GitHub-hosted execution run, workflow badge, sanitized public status, and runner-facing event belongs to `GlacierEQ/public-actions-runner-host`.

The private control plane defines policy, approvals, and immutable receipts. It may not own executable workflow YAML, overwrite a prior result, or become the visible or billed Actions owner.

## Governing sequence

```text
authorize principal
  -> verify immutable public repository identity
  -> require dedicated least-privilege bridge credentials
  -> verify private non-executing append-only control plane
  -> validate strict metadata-only envelope
  -> reject duplicate immutable job ID
  -> verify private dual approval where required
  -> ephemeral catalog-approved checkout
  -> isolated adapter on ubuntu-latest
  -> immutable detailed private receipt
  -> truthful sanitized public status
  -> governed release result
```

## Repository identity lock

Execution is valid only when GitHub reports all of the following:

- full name `GlacierEQ/public-actions-runner-host`;
- repository ID `1265621488`;
- owner login `GlacierEQ`;
- owner ID `194243768`;
- public visibility;
- default branch `main`;
- not private, archived, disabled, or forked.

## Authorized ingress

The authorized-actor policy binds login, numeric GitHub actor ID, and allowed event roles.

Permitted ingress types:

- owner-created public issue with `OWNER` association;
- owner `workflow_dispatch`;
- owner-authenticated `repository_dispatch`;
- owner push of one bounded `jobs/<job_id>.json` file.

An issue being public does not make its author an execution principal.

## Strict envelope contract

Only these fields are permitted:

```text
job_id
pillar
action
source_repo
source_ref
task
approval_id
```

Controls include:

- maximum canonical envelope size of 4096 bytes;
- strings only;
- field-specific length limits;
- unknown-field rejection;
- control-character rejection;
- hardened source-ref validation;
- catalog-derived repository allowlist;
- catalog actions may not override repository or task;
- approval IDs accepted only for pillars G and I;
- one queue file directly under `jobs/`, no symlink or traversal;
- queue filename must match the enclosed job ID.

## Public events

```text
case-evidence
document-processing
coding-deploy
evolution-optimize
memory-sync
infra-gateway
case-ops
orchestrate
intl-case-ops
media-queue
whisperx-exec
gateway-ci
comet-agent-ci
apex-verification
action-face-canary
```

## Credential contract

The public action face uses GitHub OIDC to request two short-lived, single-repository installation tokens from the Keymaster bridge:

- a control token scoped to `GlacierEQ/llm-runner-teams` with `contents:write`, used only to claim job IDs, read approvals and policies, and create immutable receipts;
- a workload token scoped to the catalog-approved source repository with `contents:read`, used only for the ephemeral source checkout.

Static `APEX_PRIVATE_READ_TOKEN`, static `APEX_CONTROL_TOKEN`, and `GH_PAT` fallbacks are forbidden. Tokens are minted only after strict planning and control-plane checks, are never exposed to workload code, and must be revoked before governed release can succeed.

Both checkouts use an immutable checkout action revision and `persist-credentials: false`. Installation-token values and `GITHUB_TOKEN` are removed from workload process environments.

## Private control-plane invariants

Before workload planning, the action face verifies that `GlacierEQ/llm-runner-teams`:

1. remains the correct private, enabled, non-forked repository on `main`;
2. contains no `.yml` or `.yaml` executable workflow;
3. has an active no-private-actions policy;
4. points execution to this public repository;
5. forbids Actions in private workloads;
6. has an active one-job-one-receipt result policy;
7. forbids receipt overwrite and deletion;
8. requires provenance and payload-hash fields.

## Replay and receipt integrity

One job ID equals one immutable private path:

```text
results/<job_id>.json
```

The public runner checks for an existing receipt before checkout. A duplicate is rejected as a replay. The publish bridge independently refuses an existing path.

A receipt binds:

- canonical payload SHA-256;
- publication timestamp;
- workflow run ID and attempt;
- public runner commit SHA;
- execution repository;
- trigger actor and actor ID;
- workload repository and source ref;
- action, task, adapter, pillar, status, and detailed findings.

Workload success without receipt success is not release success.

## Public/private output boundary

Public output is restricted to:

```text
job ID
pillar
action
task
state
private receipt state
public run URL
```

Protected source contents, evidence, legal narratives, document contents, prompts, messages, credentials, and detailed output remain private.

## Canary gate

`action-face-canary` must pass before target verification. It validates:

- Python syntax and JSON contracts;
- schema/planner field alignment;
- immutable checkout pinning;
- `ubuntu-latest` ownership;
- absence of `self-hosted`, `GH_PAT`, version-tag checkout, and third-party script-action drift;
- catalog uniqueness;
- strict planner positive/negative paths;
- authorized numeric identity and intruder denial;
- secret isolation;
- subprocess output isolation;
- replay-guard presence.

After canary success, `apex-verification` may run the target quality/function/security/hardening suite.

## Release-blocking drift

- Public action face is private or its immutable identity changes.
- Private control plane becomes public, forked, archived, disabled, or gains executable workflow YAML.
- Dedicated bridge secrets are unavailable or over-broad.
- A private repository owns the Actions run.
- An unauthorized principal reaches planning.
- An unknown or oversized envelope is accepted.
- Workload code receives bridge credentials.
- A prior private receipt can be overwritten or deleted.
- Detailed results are published publicly.
- A dual-gated operation lacks matching private approval.
- The canary or private result publication fails.

## Result ownership

The public action face owns execution identity and sanitized status. The private control plane owns detailed truth, approvals, and append-only receipts. AKOS owns the architecture. No layer may silently assume another layer's role.
