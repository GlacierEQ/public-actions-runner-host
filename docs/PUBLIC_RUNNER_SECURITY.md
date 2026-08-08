# APEX Public Runner Security Contract

This public repository provides GitHub-hosted execution capacity for the private APEX control plane.

## Trust boundary

- The public workflow receives only an opaque job ID, pillar, cataloged action or allowlisted task, and source ref.
- No evidence, legal narrative, credentials, prompts, email content, or private document content belongs in a dispatch payload.
- Results are written to the private `GlacierEQ/llm-runner-teams` repository. They are never uploaded as public workflow artifacts.
- Workload credentials are not persisted in checkout and are not exposed to workload processes.
- Commands are selected by the checked-in runner adapter. Arbitrary shell commands are rejected.
- Pillars G and I require a matching private approval record before execution.

## Canonical bridge identity

The canonical `APEX Public Action Face` uses **GitHub Actions OIDC → Keymaster**, not repository-stored GitHub App key material.

The workflow grants `id-token: write` solely so GitHub can issue the workload identity token. The Keymaster broker then verifies the exact public repository, immutable repository/owner IDs, actor, workflow identity, event, and ref before it will resolve the centrally managed GitHub App identity.

Canonical repository configuration therefore requires:

| Setting | Location | Purpose |
|---|---|---|
| `id-token: write` | Workflow permission | Permit GitHub to issue an OIDC identity for this exact job |
| `apex-github-oidc-broker` | Keymaster runtime | Verify OIDC claims and mint bounded GitHub App installation tokens |
| GitHub App private key | Keymaster/Vault reference only | Sign App JWTs inside the broker boundary |

The canonical workflow requires **no** `APEX_RUNNER_APP_CLIENT_ID` repository variable and **no** `APEX_RUNNER_APP_PRIVATE_KEY` repository secret.

The older GitHub App Manifest launcher is retained only for `.github/workflows/apex-github-app-bridge-canary.yml` compatibility/recovery testing. Its repository variable/secret contract must not be consumed by `.github/workflows/apex-pillar-runner.yml`.

For each accepted canonical job, Keymaster mints two separate installation tokens:

| Runtime token | Scope | Permission |
|---|---|---|
| `APEX_CONTROL_TOKEN` | `GlacierEQ/llm-runner-teams` only | Contents read/write for immutable claims, approvals, and results |
| `APEX_PRIVATE_READ_TOKEN` | Exactly one catalog-approved private workload repository | Contents read only |

Repository, permission, and operation are bound together in the workflow contract and regression tests. The dynamic workload repository is transferred through a step environment variable instead of being interpolated directly into shell syntax.

The tokens are never persisted by checkout or provided to the workload process. After result publication/status handling, the workflow calls GitHub's installation-token revocation endpoint separately for each minted token. A required revocation failure fails the governed release. Static PAT fallback is prohibited.

## OIDC and broker boundary

The OIDC client:

- accepts the runner-provided OIDC endpoint only over HTTPS under `*.actions.githubusercontent.com`;
- rejects every HTTP redirect before bearer credentials can be forwarded;
- requests the fixed `apex-keymaster-public-runner` audience;
- sends the GitHub OIDC assertion only to the fixed Keymaster broker endpoint;
- never persists the OIDC token or returned installation token;
- masks returned installation tokens in the Actions log stream.

The broker:

- validates issuer, audience, repository, immutable IDs, actor, workflow ref, event, and branch/ref;
- permits only repositories present in the completed Keymaster bootstrap allowlist;
- permits only the bounded GitHub permission vocabulary exposed by the broker;
- resolves the App private key only behind the Keymaster broker boundary;
- records token-mint receipts without persisting the returned installation token.

## Legacy App-bridge canary boundary

These files remain for compatibility/recovery testing only:

```text
START_APEX_RUNNER_BRIDGE.cmd
github-app/start_apex_runner_bridge.ps1
github-app/bootstrap_apex_github_app.py
.github/workflows/apex-github-app-bridge-canary.yml
github-app/bridge-contract.json
```

That lane still forbids manual PEM generation, download, copy/paste, and chat transport. It is not the canonical credential path for the public runner.

## Private control-plane paths

- `claims/{job_id}.json`: immutable pre-execution job claims.
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

Public logs report sanitized status only. Detailed command output, private file names, and manifests are returned to the private control plane.

## Completion boundary

Merged code and green tests do not prove the OIDC bridge is operational. Activation requires a real workflow execution in which OIDC authentication, both Keymaster token mints, exact private workload checkout, integrity binding, adapter execution, private receipt publication, and both token revocations all complete successfully.

The live private receipt—not the presence of configuration—is the operational completion proof.
