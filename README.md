# APEX Public Action Face

`GlacierEQ/public-actions-runner-host` implements the current hardened **public GitHub Actions execution route** for APEX/GlacierEQ workloads.

**Project direction: OPERATOR.** This repository is an execution surface, not the project sovereign. AKOS, this runner, the private receipt plane, CI, catalogs, topology, and receipts cannot acquire project-direction authority from being selected, persisted, or verified.

```text
OPERATOR-DIRECTED OPERATION
  -> exact public execution-route identity check
  -> strict metadata-only envelope
  -> GitHub Actions OIDC identity
  -> Keymaster broker
  -> short-lived one-repository installation tokens
  -> required private receipt/control-plane checks
  -> duplicate-job replay guard
  -> ephemeral allowlisted workload checkout
  -> isolated adapter on ubuntu-latest
  -> detailed private receipt
  -> explicit installation-token revocation
  -> truthful sanitized public status
```

## Start the current OIDC bridge

The current `APEX Public Action Face` does **not** require a repository-stored GitHub App Client ID, private key, PAT, HMAC secret, or PEM handoff for its primary token-mint path.

The workflow requests a GitHub Actions OIDC identity (`id-token: write`) and exchanges it with the Keymaster broker. Keymaster validates the expected repository/workflow/actor claims, resolves the managed GitHub App identity behind secret-store references, and mints only the short-lived one-repository token required for the current operation. The public runner never receives the App private key.

The older launcher and GitHub App Manifest bootstrap remain only for the dedicated legacy App-bridge canary/recovery lane:

```text
START_APEX_RUNNER_BRIDGE.cmd
github-app/start_apex_runner_bridge.ps1
python github-app/bootstrap_apex_github_app.py
.github/workflows/apex-github-app-bridge-canary.yml
```

That legacy lane is compatibility/recovery infrastructure. It is not a source of project authority.

## Current technical split

| Surface | Repository | Technical responsibility | Non-authority boundary |
|---|---|---|---|
| Public execution route | `GlacierEQ/public-actions-runner-host` | Actions runs, sanitized status, allowlisted execution | Does not own project direction or peer lifecycle |
| Private receipt/control plane | `GlacierEQ/llm-runner-teams` | Policies used by this route, approvals, append-oriented detailed receipts | Storage/approval does not create sovereignty |
| AKOS | `GlacierEQ/AKOS` | Optional architecture/cognition/verification support | Does not govern this runner or the estate |
| Workload repositories | Operator-selected/route-admitted repos | Workload implementation | Admission does not subordinate them |

```text
selection != ownership
routing != sovereignty
receipt != project authority
technical_permission != project_direction
```

## Fail-closed execution identity

The public runner binds the exact identity required to prove that a run came from this implementation, including repository/owner identity, visibility, expected workflow, actor identity, accepted event/ref boundary, and the configured execution branch.

A mismatch blocks **this route** from claiming a valid run. These technical identity checks do not make the configured branch or repository a permanent project hierarchy.

## Authorized ingress

Execution ingress is limited to identities and event roles admitted by `config/authorized-actors.json` for this route.

Supported routes include:

- owner-created `[APEX JOB] <job_id>` public issue;
- owner `workflow_dispatch`;
- owner-authenticated `repository_dispatch`;
- owner push of one bounded `jobs/<job_id>.json` envelope.

Public issue authors are not execution principals merely because the issue is public.

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

Unknown fields, control characters, conflicting declarations, oversized payloads, path traversal, arbitrary repositories, and catalog-action overrides are rejected before checkout.

Catalog admission says what this runner can execute safely. It does not rank, publish, suppress, retire, merge, or govern repositories.

## Supported lanes

Current event/routing lanes include case/evidence, document processing, coding/deploy, evolution/optimization, memory, infrastructure, orchestration, and verification workloads. Their names are routing metadata, not project hierarchy.

## Keymaster OIDC credential bridge

No broad PAT fallback is allowed for the primary path.

```text
GitHub-hosted runner
  -> GitHub OIDC token
  -> exact-identity Keymaster broker
  -> Vault-backed GitHub App identity
  -> one repository + minimum permissions
  -> short-lived installation token
  -> operation
  -> explicit token revocation
```

At runtime the workflow mints narrowly scoped tokens for the receipt/control-plane repository and the selected workload repository. Workload code does not receive the App private key or runtime tokens. Checkouts use immutable action revisions and `persist-credentials: false`.

Possessing a token or admin permission is a technical capability boundary, never project-direction authority.

## Private receipt/control-plane gate

Before planning a workload, the public face verifies the restrictions this route depends on in `GlacierEQ/llm-runner-teams`, including private/non-executing control-plane behavior and append-oriented result handling.

Failure blocks this route's verification claim. It does **not** prove that the Operator's objective is invalid or that no other authorized route can execute it.

## Replay and receipt integrity

One job ID produces one detailed private receipt path for this route:

```text
results/<job_id>.json
```

A duplicate job ID is rejected before workload checkout. Receipts bind the execution identity, relevant source revision, actor/run identity, payload digest, and result scope.

A receipt proves the event it records. Persistence of a receipt does not create future permission or project authority.

## Canary before workload trust

Activation remains two-stage:

1. `action-face-canary` tests syntax/contracts, immutable action pinning, secret isolation, workflow invariants, catalog consistency, strict planning, identity rejection paths, and replay controls.
2. `apex-verification` runs the selected target quality/function/security/hardening suite after the route's canary requirements are satisfied.

A green gate proves the tested mechanism only. It does not become a project decision-maker.

## Public truth boundary

Public status contains only the minimum safe execution metadata. Evidence, legal narratives, source contents, prompts, messages, credentials, document contents, and detailed logs remain private unless separately authorized for disclosure.

## Core files

- [Execution workflow](.github/workflows/apex-pillar-runner.yml)
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
- [Legacy App-bridge Bootstrap](github-app/ACTIVATION.md)

## Current activation condition

The OIDC/Keymaster implementation is code-complete only when its repository checks are green; **operational activation additionally requires a real action-face run that successfully mints the narrow tokens, checks out the exact selected workload revision, executes, publishes the required private receipt, revokes the tokens, and leaves truthful readback evidence.**

Until that live receipt exists, do not describe the path as operationally complete.

## Governing implementation principle

**Security controls constrain how this route executes. They do not acquire authority to redefine what the Operator is trying to accomplish.**
