# APEX Public Action Face — Canary Protocol

## Purpose

Activation occurs in two controlled stages. The public runner is never trusted merely because the workflow file exists.

## Preconditions

1. `GlacierEQ/public-actions-runner-host` is public.
2. `GlacierEQ/llm-runner-teams` remains private.
3. `GlacierEQ/llm-runner-teams/.github/workflows/` contains no `.yml` or `.yaml` file.
4. `APEX_PRIVATE_READ_TOKEN` and `APEX_CONTROL_TOKEN` satisfy `config/required-secrets.json`.
5. The public workflow uses the immutable checkout revision recorded in the canary.
6. No private repository owns a GitHub Actions run.

## Stage 1 — Action-face self-canary

Dispatch a unique job ID:

```json
{
  "event_type": "action-face-canary",
  "client_payload": {
    "job_id": "canary-YYYYMMDD-NNN",
    "source_ref": "main"
  }
}
```

The canary checks:

- bridge-token names are absent from workload execution;
- all public runner Python scripts compile;
- all JSON contracts parse;
- job-envelope schema fields match the planner contract;
- the canonical workflow is `ubuntu-latest` only;
- checkout is pinned to an immutable commit;
- no `GH_PAT`, `self-hosted`, version-tag checkout, or `actions/github-script` drift exists;
- catalog action keys are unique and target only `GlacierEQ/*` repositories;
- valid plans pass and unknown fields fail;
- authorized numeric actor identity passes and an intruder fails;
- canary subprocess output is isolated from the live workflow output file.

### Stage 1 acceptance

- Public workflow conclusion: success.
- Private immutable result: `results/<job_id>.json` exists.
- Receipt contains payload hash, run ID, run attempt, execution repository, and public runner SHA.
- Public issue or run output contains no protected workload details.
- Reusing the same job ID is blocked before workload checkout.

## Stage 2 — Target verification

After Stage 1 passes, dispatch:

```json
{
  "event_type": "apex-verification",
  "client_payload": {
    "job_id": "verify-YYYYMMDD-NNN",
    "source_ref": "main"
  }
}
```

This checks out `GlacierEQ/mastermind` through the dedicated read token and runs the APEX quality/function/security/hardening suite with no bridge token in the workload environment.

### Stage 2 acceptance

- APEX verification process completes.
- A structured verification report is embedded in the immutable private result.
- Quality, function, security, and hardening findings are reviewed.
- Deployment release decision is updated from evidence, not workflow status alone.

## Failure rules

- Visibility mismatch: block before planning.
- Unauthorized principal: block before planning.
- Missing dedicated token: block before control-plane access or checkout.
- Private workflow drift: block before planning.
- Invalid or oversized envelope: close as not planned; no checkout.
- Existing result path: replay block; preserve prior receipt.
- Workload failure: publish the failure privately and keep the public issue open.
- Private result publication failure: block release even when workload execution succeeded.

## Evidence to retain

```text
public workflow run URL
job ID
source repository and ref
public runner commit SHA
private result path
private receipt payload hash
release decision
```

Never retain secret values in evidence.
