# AKOS + Echo Policy Enforcement — v0.1.0-rc.1

This file is the permanent default-branch pointer to the first frozen AKOS + Echo hardened connector-policy release candidate.

## Open it

- **Release-candidate branch:** [release/akos-echo-policy-v0.1.0-rc1](https://github.com/GlacierEQ/FILEBOSS/tree/release/akos-echo-policy-v0.1.0-rc1)
- **Exact frozen commit:** [77668e873653939904355994f0dc94f7ef85edc0](https://github.com/GlacierEQ/FILEBOSS/commit/77668e873653939904355994f0dc94f7ef85edc0)
- **Immutable ZIP:** [AKOS + Echo v0.1.0-rc.1.zip](https://github.com/GlacierEQ/FILEBOSS/archive/77668e873653939904355994f0dc94f7ef85edc0.zip)
- **Immutable TAR.GZ:** [AKOS + Echo v0.1.0-rc.1.tar.gz](https://github.com/GlacierEQ/FILEBOSS/archive/77668e873653939904355994f0dc94f7ef85edc0.tar.gz)
- **Source PR:** [#52](https://github.com/GlacierEQ/FILEBOSS/pull/52)
- **Promotion ledger:** [#56](https://github.com/GlacierEQ/FILEBOSS/issues/56)

## Exact identity

- Frozen commit: `77668e873653939904355994f0dc94f7ef85edc0`
- Public provenance: **23/23 copied Git blobs matched the exact private commit**
- Hosted focused gate: **50 passed, 0 failed**
- Public validation run: [30341778921](https://github.com/GlacierEQ/public-actions-runner-host/actions/runs/30341778921)
- Immutable dispatch record: [public-actions-runner-host#29](https://github.com/GlacierEQ/public-actions-runner-host/issues/29)

## What exists

The release candidate contains the executable fail-closed connector-policy gateway, policy and route-authority verification, protected external policy-digest trust, HMAC-bound attestations, primary/fallback routing controls, preflight and postflight proof enforcement, structured outcome validation, rejected receipts, a tamper-detecting repair queue, mandatory remote-MCP and Actor Violations integration, runtime-only credential configuration, deployment bootstrap checks, documentation, and the focused test suite.

## Status

This release candidate is **real, named, frozen, recoverable, visible from `main`, and directly downloadable by exact commit**. It is not being represented as production-ready yet.

Promotion remains tracked in issue #56. Required work includes repairing all validated current-head findings, correcting the public runner's false-success reporting path, provisioning deployment runtime values, rotating historically exposed credentials, rerunning the exact-byte gate, obtaining a clean final review, and then promoting the final head to `main`.
