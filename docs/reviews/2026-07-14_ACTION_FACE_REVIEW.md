# Public Action Face Review — 2026-07-14

## Scope

Review the migration that makes `GlacierEQ/public-actions-runner-host` the sole GitHub Actions face while retaining `GlacierEQ/llm-runner-teams` as the private non-executing control plane.

## Bound revisions

- Public action face reviewed head: `7ec193d811fd5d25d7f7de3aac4682367f76ba33`
- Private control plane reviewed head: `36c9281fd10ba3c0a1934c241f1118132aed12af`
- AKOS architecture record: `e3d3ce7d22d331297fc51390452b41efc7759e2a`

## Quality review

### Passed

- Public and private responsibilities are explicitly separated.
- The public workflow has one canonical execution identity: `APEX Public Action Face`.
- Fourteen public events are registered: nine pillar events plus media queue, WhisperX, Gateway CI, Comet CI, and APEX verification.
- Migrated actions are cataloged rather than implemented through ad hoc workflow forks.
- Public issue, push-queue, manual, and repository-dispatch ingress normalize into one plan contract.
- The thirteen former private workflow paths and blob SHAs are preserved in a migration record.
- The private workflows were removed from current `main`; Git history remains the preservation source.

### Limitations

- No live public-host run has been executed because GitHub currently reports this repository as private.
- Runtime behavior is therefore not certified until the repository is public and one controlled smoke job completes.

## Function review

### Passed by static inspection

- The public workflow runs on `ubuntu-latest`.
- It validates the canonical repository and required public visibility before workload execution.
- It supports allowlisted base tasks and catalog actions.
- It checks out approved private workloads without persisting credentials.
- It supports dedicated Node CI, Python CI, and APEX verification adapters.
- It returns detailed results to the private control plane and publishes sanitized issue status only.
- Pillars G and I verify an exact private approval record before execution.

### Runtime evidence still required

1. Public visibility guard passes.
2. Private workload checkout succeeds with minimum-scope token.
3. APEX verification action writes a detailed private result.
4. Public issue output contains only sanitized fields.
5. Missing-token and failed-adapter paths fail closed.

## Security review

### Passed

- Control-plane credentials are not job-scoped.
- `APEX_CONTROL_TOKEN` is available only to approval verification and private result publication steps.
- Workload code executes without the control-plane token.
- Checkout uses `persist-credentials: false` for both runner and workload repositories.
- Public payload contract prohibits evidence, document contents, prompts, messages, credentials, private logs, and detailed output.
- Public visibility is enforced by an API-backed guard.
- Detailed result ownership remains private.
- Dual-confirmation data for legal pillars remains private.

### Residual risks

- `GH_PAT` is an approved fallback; minimum scopes must remain enforced operationally.
- A public issue can still contain inappropriate content if a human ignores policy. Issue intake validation should remain metadata-only and fail on unexpected fields in a future hardening cycle.
- Dependency installation executes third-party package lifecycle behavior inside an ephemeral GitHub-hosted runner. No private control token is present during this stage, limiting impact.

## Hardening review

### Passed

- Missing executables are converted into controlled blocked results.
- Process start errors are captured.
- Node/Python CI timeouts are captured.
- APEX verification has a one-hour timeout and controlled failure record.
- Output is hashed and tailed instead of indiscriminately published.
- TypeScript local-binary absence fails closed.
- The public host blocks execution when repository identity or visibility drifts.
- The private control plane has an explicit no-private-actions policy and documentation-only workflows directory.

## Migration verification

The private control-plane comparison from `427f3839391e97a59d7cb2c818802b260dc6b306` to `36c9281fd10ba3c0a1934c241f1118132aed12af` shows all thirteen executable workflow YAML files removed and the control-plane policy, migration record, and documentation added.

The public action-face comparison from `20e0ddc8143abb89d4a8ce827702c694795dca69` to `7ec193d811fd5d25d7f7de3aac4682367f76ba33` shows the canonical workflow updated and eight action-face contract, planner, guard, and adapter files added.

## Release decision

```text
Architecture ownership: PASS
Code and policy migration: PASS
Security hardening: PASS WITH RUNTIME VERIFICATION PENDING
Execution activation: BLOCK
Deployment reliance: BLOCK
```

## Blocking condition

GitHub currently reports `GlacierEQ/public-actions-runner-host` visibility as `private`. It must be changed to `public` before any action-face run is accepted as valid.

## Activation test

After visibility is public, submit one metadata-only job:

```json
{
  "event_type": "apex-verification",
  "client_payload": {
    "job_id": "verify-20260714-001",
    "source_ref": "main"
  }
}
```

Release remains blocked until the public run completes and the detailed private result is reviewed.
