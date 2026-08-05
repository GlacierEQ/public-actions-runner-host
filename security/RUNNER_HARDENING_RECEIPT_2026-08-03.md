# Public Runner Hardening Receipt — 2026-08-03

## Scope

This receipt records the final candidate hardening state for the public specialized GitHub Actions runner. It does not activate private-source execution and does not claim owner-only GitHub App credentials exist.

## Implemented boundaries

- Explicit capability-minimized workload environments; no ambient GitHub, Actions, APEX, OIDC, output-file, App-key, PAT, or unrelated policy authority.
- Immutable full commit SHA requirement for specialized Monolith Code, Docs, and Analysis actions.
- Flat catalog and active domain-registry reconciliation for repository, adapter identity, execution mode, token ceiling, credential exposure, and source-write policy.
- Held Linux directory descriptors with `O_NOFOLLOW` for runner, control, workload, result-directory, and relative-file access.
- Checkout identity bound to device and inode for the full operation; visible path replacement blocks execution or publication.
- Pre- and post-execution Git attestation of exact private source SHA and clean tracked state.
- No-follow tracked-file reads for Docs and Analysis.
- No-follow recursive media, PDF, and office-document inventory; symlink entries are rejected without reading targets.
- Streamed content hashing, fixed-prefix PDF inspection, bounded embedded records, deterministic full-inventory manifest hashes, and inventory size ceilings.
- Post-run result verification and publication reread through held descriptors; byte changes or symlink replacement fail closed.
- Public canary positive and negative planning paths for all three specialized Monolith actions.
- Independent runner-team workflow now triggers on runner security implementation changes.

## Adversarial tests

The repository-owned suite includes real tests for:

- direct checkout symlink;
- symlinked checkout parent;
- file-level external symlink;
- tracked document symlink;
- visible checkout replacement while a descriptor is held;
- commit drift;
- tracked source mutation by an executed subprocess;
- untracked runtime artifacts;
- poisoned GitHub, Actions, APEX, OIDC, App-key, and output-file environments;
- mutable specialized source refs;
- bounded media receipts;
- streamed multi-megabyte PDF inspection;
- result symlink replacement and post-guard byte changes.

## Local candidate verification

The exact branch candidate passed:

```text
bash scripts/ci/public-action-face-ci.sh
```

The standalone `action_face_selftest.run(...)` canary completed with zero failed checks against the same branch candidate.

## Release boundary at issuance

Merge remained contingent on successful GitHub-hosted `CI` and `Public Runner Team Contract` checks attached to this receipt commit.

At the time this receipt was issued, live private-source execution was blocked until these repository settings existed:

```text
APEX_RUNNER_APP_CLIENT_ID
APEX_RUNNER_APP_PRIVATE_KEY
```

No private key was stored, generated, requested, or exposed by the 2026-08-03 hardening change.

## Superseding activation record — 2026-08-05

The earlier manual owner-configuration implication is superseded.

Merged PRs #87, #88, and #89 established the canonical automated ignition path:

```text
START_APEX_RUNNER_BRIDGE.cmd
  -> github-app/start_apex_runner_bridge.ps1
  -> github-app/bootstrap_apex_github_app.py
  -> GitHub App Manifest flow
  -> direct repository variable and secret writes
  -> exact installation verification
  -> workflow rerun and completion-contract validation
```

The owner no longer generates, views, downloads, copies, pastes, stores, or transmits the App private key. The only human boundary is GitHub's own account-consent and selected-repository installation approval screen.

This supersession does not claim the bridge is active. Activation remains unverified until the launcher completes and a new private workload rerun produces an independently verified bounded receipt.
