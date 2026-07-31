# Monolith Private Runner Recovery

**Status:** executable paths built; GitHub account activation required

## Verified diagnosis

The `GlacierEQ/monolith` private-repository workflow creates a run and a `validate` job, but the job completes with failure before GitHub records any step, runner image, or downloadable job log. The same workflow still fails after removing every reusable action and making the first step a runner-start proof.

A control run in the public repository `GlacierEQ/public-actions-runner-host` successfully allocated GitHub-hosted runner `2.336.0` on Ubuntu 24.04 and executed normal steps. This rules out a general GitHub-hosted runner outage and rules out Monolith's Python suite as the pre-start failure.

The remaining native-runner causes are GitHub account/repository controls, principally:

1. private-repository Actions minutes, budget, payment, or storage billing block;
2. a GitHub-controlled or repository-level Actions-disabled state.

## Recovery path A — restore native private Actions

The GlacierEQ account owner must inspect GitHub settings that are not exposed by the connected repository API:

1. Open **Settings → Billing and licensing → Usage**.
2. Filter usage to **GitHub Actions**.
3. Confirm that private-repository minutes and Actions storage are not exhausted or blocked.
4. Open **Budgets and alerts** and confirm the Actions budget does not stop usage at zero or at an exhausted threshold.
5. Confirm a valid payment method when usage exceeds the included quota.
6. Open `GlacierEQ/monolith` → **Settings → Actions → General**.
7. Confirm Actions are enabled and standard GitHub-hosted runners are permitted.
8. Re-run **IP Governance Gate**.

### Native acceptance evidence

The native path is restored only when the Monolith run records all of the following:

```text
Set up job = success
Prove runner startup = success
Validate checked-in manifest = success
Run complete test suite = success
Verify JSON syntax = success
workflow conclusion = success
```

## Recovery path B — activate the public/private GitHub App bridge

The public runner is already operational. Its private workload bridge correctly failed closed because these values are absent:

```text
Actions variable: APEX_RUNNER_APP_CLIENT_ID
Actions secret:   APEX_RUNNER_APP_PRIVATE_KEY
```

Activate the private GitHub App defined in `github-app/ACTIVATION.md` and install it on **Only select repositories**:

```text
GlacierEQ/public-actions-runner-host
GlacierEQ/llm-runner-teams
GlacierEQ/monolith
```

Required repository permission:

```text
Contents: read and write
```

All other repository, organization, and account permissions remain disabled. Runtime tokens are down-scoped to:

- `contents:write` for `GlacierEQ/llm-runner-teams` only;
- `contents:read` for `GlacierEQ/monolith` only.

Store the App client ID as the repository variable and the complete PEM private key as the repository secret in `GlacierEQ/public-actions-runner-host`.

Then synchronize PR #62 or re-run its **APEX Public Action Face** workflow.

### Bridge acceptance evidence

```text
Require GitHub App bridge configuration = success
Mint one-repository private control token = success
Assert private non-executing control plane = success
Atomically claim immutable job ID = success
Mint one-repository private workload token = success
Checkout catalog-approved workload = success
Bind exact workload repository and commit = success
Execute isolated public action adapter = success
Return verified detailed result to private control plane = success
workflow conclusion = success
```

## Security invariants

- No personal access token fallback.
- No private key in Git history, issues, pull requests, artifacts, or chat.
- No installation on all repositories.
- No Actions, administration, secrets, workflow, or deletion permission.
- Workload subprocesses receive no bridge token.
- The exact source commit is bound before execution.
- Detailed private output returns only through the immutable private receipt plane.
