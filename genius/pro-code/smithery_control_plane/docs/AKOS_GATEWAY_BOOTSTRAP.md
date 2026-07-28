# AKOS Gateway Protected Runtime Bootstrap

**Classification:** systems-control; not case evidence.

AKOS intentionally fails closed unless its trust material is supplied by the
runtime. No credential or executable trust-root value belongs in the repository.

## Required protected variables

| Variable | Purpose | Storage |
|---|---|---|
| `AKOS_POLICY_SHA256` | Independently approved SHA-256 of the policy file | GitHub Actions secret and deployment secret |
| `AKOS_ATTESTATION_HMAC_KEY` | Signs current-request identity, affinity, and runtime-probe attestations | Runtime secret only; use at least 32 random bytes |
| `AKOS_TENANT_ALIAS` | Privacy-safe alias for the authorized control account | Runtime variable or secret |

## Optional variables

- `AKOS_REPAIR_QUEUE`: append-only JSONL repair queue path.
- `FILEBOSS_ALLOWED_ROOTS`: operating-system-path-separated local roots.
- `MEMORY_PLUGIN_TOKEN_A` and `MEMORY_PLUGIN_TOKEN_B`: runtime-only memory
  connector credentials. Rotate any values previously committed to Git history.
- `CONTEXT_GLOBAL` and `CONTEXT_DIRECT`: runtime-only context aliases. Rotate any
  previously committed values.

## GitHub Actions installation

1. Review and approve the policy digest independently.
2. Store it as the protected repository secret `AKOS_POLICY_SHA256`.
3. Install the exact reviewed candidate from
   `smithery_control_plane/ci/akos-connector-policy.yml` at
   `.github/workflows/akos-connector-policy.yml`.
4. Confirm `Validate AKOS connector policy` passes on PR #52.
5. Make that check required for `main` before merging.

The candidate consumes the protected GitHub Actions secret. It does not contain
an executable digest literal.

## Railway or another deployment platform

Create the three required variables in the platform's protected environment.
Do not commit their values to `railway-mcp.json`, Docker files, MCP configuration,
or documentation. Restart the service and verify `/health` before enabling tool
traffic.
