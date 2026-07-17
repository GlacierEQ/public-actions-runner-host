# APEX Public Action Face — Canary Protocol

## Purpose

Activation occurs in two controlled stages. The public runner is never trusted merely because workflow code exists.

## Preconditions

1. `GlacierEQ/public-actions-runner-host` is public and still matches its bound repository and owner IDs.
2. `GlacierEQ/llm-runner-teams` remains private, enabled, non-forked, and on `main`.
3. `GlacierEQ/llm-runner-teams/.github/workflows/` contains no `.yml` or `.yaml` file.
4. `policy/no-private-actions.json` and `policy/immutable-results.json` remain active.
5. `APEX_PRIVATE_READ_TOKEN` and `APEX_CONTROL_TOKEN` satisfy `config/required-secrets.json`.
6. The public workflow uses the immutable checkout revision recorded in the canary.
7. No private repository owns a GitHub Actions run.

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

### Stage 1 execution sequence

```text
authorize owner identity
→ verify immutable public repository identity and visibility
→ verify dedicated bridge credentials
→ verify private control-plane invariants
→ validate strict envelope
→ atomically create claims/<job_id>.json
→ checkout the public action face as the canary workload
→ verify origin, clean tracked state, and exact commit SHA
→ run security and contract self-tests with bridge tokens removed
→ publish immutable results/<job_id>.json
→ publish sanitized public state
→ enforce success only when no synthesized block result was needed
```

### Canary checks

- bridge-token names and `GITHUB_TOKEN` are absent from workload execution;
- all public runner Python scripts compile;
- all JSON contracts parse;
- job-envelope schema fields match the planner contract;
- the canonical workflow is `ubuntu-latest` only;
- both checkout uses are pinned to the approved immutable commit;
- no `GH_PAT`, `self-hosted`, version-tag checkout, or `actions/github-script` drift exists;
- strict authorization accepts the bound owner login and numeric ID and rejects an intruder;
- strict planning accepts the canary envelope and rejects an unknown field;
- catalog action keys are unique and target only `GlacierEQ/*` repositories;
- atomic-claim, append-only-receipt, and replay-block wiring remain present;
- checkout binding verifies origin repository, clean tracked state, and exact `HEAD` SHA;
- synthesized blocked results are explicitly signaled and can never produce a green conclusion;
- canary subprocess outputs are isolated from the live workflow output file.

### Stage 1 acceptance

- Public workflow conclusion: success.
- Private immutable claim: `claims/<job_id>.json` exists.
- Private immutable result: `results/<job_id>.json` exists.
- Claim records canonical plan SHA and execution provenance.
- Result and receipt record the exact resolved workload commit SHA.
- Receipt contains payload hash, claim path/blob SHA, plan hash, run ID, run attempt, execution repository, and public runner SHA.
- Result provenance matches the claim provenance.
- The synthesizer reports `synthesized=false`.
- Public output contains no protected workload details.
- Reusing the same job ID is blocked before checkout and preserves the earlier claim/result.

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

This checks out `GlacierEQ/mastermind` through the dedicated read token, binds the checkout to its exact repository and commit SHA, and runs the APEX quality/function/security/hardening suite with no bridge token in the workload environment.

### Stage 2 acceptance

- A unique private claim exists before checkout.
- Checkout origin is `GlacierEQ/mastermind` and the exact commit SHA is recorded.
- APEX verification completes without a synthesized lifecycle result.
- A structured verification report is embedded in the immutable private result.
- The receipt binds the result to the claim, plan hash, exact workload SHA, public runner SHA, and workflow run.
- Quality, function, security, and hardening findings are reviewed.
- Deployment release decision is updated from evidence, not workflow status alone.

## Failure rules

- Visibility or immutable identity mismatch: block before planning.
- Unauthorized principal: block before planning.
- Missing dedicated token: block before control-plane access or checkout.
- Private workflow or policy drift: block before planning.
- Invalid, oversized, conflicting, or mixed-path envelope: close as not planned; no claim or checkout.
- Existing claim or result: replay block; preserve prior private evidence.
- Approval denial, checkout failure, or checkout-binding failure after claim: synthesize and publish a private blocked lifecycle result when the workflow remains operational.
- Adapter failure: publish the detailed failure privately and keep the public issue open.
- Adapter success without a result file: synthesize a blocked result and force a failed workflow conclusion.
- Private result publication failure: block release even when workload execution succeeded.
- Abrupt infrastructure interruption after claim: preserve the incomplete claim; retry with a new job ID.

## Evidence to retain

```text
public workflow run URL
job ID
private claim path and claim blob SHA
source repository and requested ref
exact resolved source commit SHA
public runner commit SHA
private result path
private receipt payload SHA-256
workflow run ID and attempt
release decision
```

Never retain secret values in evidence.
