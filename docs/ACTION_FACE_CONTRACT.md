# APEX Public Action Face Contract

**Status:** Active technical architecture; operational activation requires live provider evidence.  
**Project-direction authority:** **OPERATOR**

## Purpose

`GlacierEQ/public-actions-runner-host` provides a hardened public GitHub Actions execution surface for Operator-directed workloads. This contract defines technical identity, ingress, token, isolation, replay, receipt, and disclosure controls.

It does **not** appoint this repository, AKOS, the private receipt plane, a workflow, a catalog, CI, or a receipt as project authority. Current routing is a technical implementation choice and may be replaced or composed under Operator direction when a stronger verified execution path exists.

## Current technical roles

| Surface | Repository | Technical responsibility | Explicit non-authority |
|---|---|---|---|
| Public action face | `GlacierEQ/public-actions-runner-host` | Current public GitHub Actions execution and sanitized status surface | Does not own project direction or peer lifecycle |
| Private receipt/control plane | `GlacierEQ/llm-runner-teams` | Policies used by this execution path, approvals, append-oriented detailed receipts | Storage/approval records do not become project authority |
| Workload repositories | Operator-selected/catalog-admitted `GlacierEQ/*` repositories | Source/workload implementation | Admission does not subordinate the repository |
| AKOS | `GlacierEQ/AKOS` | Optional architecture/cognition/verification peer | Does not govern this repository or the estate |

```text
selection != ownership
routing != sovereignty
receipt != project authority
technical permission != project direction
```

## Execution sequence

```text
Operator-directed operation
  -> authorize principal for this technical route
  -> verify exact public execution-repository identity
  -> obtain least-privilege short-lived credentials
  -> validate receipt/control-plane invariants required by this route
  -> validate strict metadata-only envelope
  -> reject replayed job ID
  -> verify required approval evidence for the requested operation
  -> ephemeral allowed workload checkout
  -> isolated adapter on ubuntu-latest
  -> detailed private receipt
  -> truthful sanitized public status
  -> technical result
```

The sequence validates this execution path. Passing it does not grant future project authority or authorize unrelated repository actions.

## Repository identity lock

A run claiming to be this action-face implementation is valid only when GitHub reports the expected technical identity:

- full name `GlacierEQ/public-actions-runner-host`;
- repository ID `1265621488`;
- owner login `GlacierEQ`;
- owner ID `194243768`;
- public visibility;
- configured execution branch `main`;
- repository is not archived, disabled, or forked.

These fields prevent impersonation of this execution route. They do not make `main`, this repository, or this contract the Operator's permanent project hierarchy.

## Authorized ingress

The authorized-actor policy binds login, numeric GitHub actor ID, and allowed event roles for this technical execution surface.

Permitted ingress types include:

- owner-created public issue with the required association;
- owner `workflow_dispatch`;
- owner-authenticated `repository_dispatch`;
- owner push of one bounded `jobs/<job_id>.json` file.

An issue being public does not make its author an execution principal. Technical ingress authorization applies only to the requested operation and does not create project-direction authority.

## Strict envelope contract

Only these fields are admitted by the current planner:

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

- maximum envelope size of 4096 bytes;
- strings only;
- field-specific length limits;
- unknown-field rejection;
- control-character rejection;
- hardened source-ref validation;
- catalog-derived repository allowlist for this route;
- catalog actions may not silently override repository or task;
- approval IDs accepted only where the configured operation requires them;
- one queue file directly under `jobs/`, no symlink or traversal;
- queue filename must match the enclosed job ID.

Catalog admission describes what this runner can execute safely. It does not rank, retire, suppress, publish, or govern repositories.

## Public events

Current event names include:

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

Event names are routing metadata only.

## Credential contract

The action face uses GitHub OIDC to request two short-lived, single-repository installation tokens from the Keymaster bridge:

- a control token scoped to `GlacierEQ/llm-runner-teams` with the minimum permissions needed by this route;
- a workload token scoped to the selected source repository with `contents:read` for ephemeral checkout.

Static broad-token fallbacks are forbidden for this path. Tokens are minted after strict planning and control-plane checks, are never exposed to workload code, and are revoked after use.

Both checkouts use immutable action revisions and `persist-credentials: false`. Installation-token values and `GITHUB_TOKEN` are removed from workload process environments.

Credential possession or repository admin permission is a technical capability boundary, never project-direction authority.

## Private receipt/control-plane invariants

Before workload planning, the action face verifies the properties this route depends on, including that `GlacierEQ/llm-runner-teams` remains appropriately restricted, avoids unintended private Actions execution, and preserves append-oriented receipt semantics.

Failure of those checks blocks **this execution route's claim**. It does not make the control plane the project sovereign, and it does not prove that no other Operator-authorized route can execute the objective.

## Replay and receipt integrity

One job ID maps to one detailed receipt path for this route:

```text
results/<job_id>.json
```

The runner checks for an existing receipt before checkout and refuses accidental overwrite. A receipt binds material execution identity such as payload digest, publication time, run/attempt, runner revision, actor identity, source repository/ref, action/task/adapter/pillar, status, and findings.

Workload success without required receipt publication means this route cannot claim a completed governed execution. A receipt proves the event and scope it records; it does not grant authority over future work.

## Public/private output boundary

Public output is restricted to the minimum safe execution status. Protected source contents, evidence, legal narratives, document contents, prompts, messages, credentials, and detailed output remain private unless the Operator intentionally authorizes another disclosure path.

## Canary gate

`action-face-canary` validates this implementation's technical safety properties, including syntax/contracts, immutable action pinning, planner rejection paths, authorized identity, secret isolation, subprocess isolation, catalog consistency, and replay controls.

After canary success, `apex-verification` may run the target quality/function/security/hardening suite when that route is selected.

A green canary or CI result proves the tested mechanism only. It does not authorize a different project action.

## Route-blocking drift

This execution route must fail closed when its required identity/security invariants fail, for example when:

- public action-face identity no longer matches the expected technical identity;
- required receipt/control-plane restrictions are violated;
- credentials are unavailable, over-broad, or exposed to workload code;
- an unauthorized principal reaches planning;
- an unknown/oversized envelope is accepted;
- an immutable receipt can be overwritten unexpectedly;
- restricted detailed results are published publicly;
- an operation requiring approval lacks matching evidence;
- required canary or receipt publication fails.

These are route-level safety failures, not project-direction decisions.

## Ownership language boundary

Avoid using `owns` to mean project decision rights. The accurate model is:

- this repository **implements** the current public action-face execution path;
- the private receipt plane **stores** detailed receipts/approval records used by that path;
- AKOS **may support** architecture, cognition, and verification;
- workload repositories **retain their own implementation state**;
- the **Operator retains project direction**.

No layer may silently convert technical responsibility into authority over another layer.
