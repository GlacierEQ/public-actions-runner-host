# Retired Self-Hosted Runner Container Archive

> **STATUS: RETIRED — ARCHIVED FOR HISTORICAL PROVENANCE ONLY.**

Self-hosted Docker runner deployment is **retired** for `GlacierEQ/public-actions-runner-host`.

All active workflows in this repository execute exclusively on ephemeral GitHub-hosted runners (`ubuntu-latest` / `ubuntu-slim`) with strict job-level isolation between unprivileged workload execution and post-execution credentialed publication.

## Hardening Applied in Archive

Even in archived form, `Dockerfile` and `entrypoint.sh` have been hardened against supply-chain and runner-persistence risks:

- Replaced the unverified remote shell installer (`drop.yannick.io/tectonic.sh | sh`) with the immutable GitHub release `tectonic-0.15.0-x86_64-unknown-linux-gnu.tar.gz` verified via `sha256sum --check --strict`.
- Added strict SHA-256 verification for `actions-runner-linux-x64-2.317.0.tar.gz`.
- Pinned all Python package versions (`duckdb`, `requests`, `supabase`, `notion-client`).
- Enforced single-job `--ephemeral` runner registration, scrubbed `RUNNER_TOKEN` from `./run.sh`, and added full workspace/credential teardown on exit.
