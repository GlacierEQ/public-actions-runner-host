# Private Legal Repository Validation

GitHub Actions are forbidden inside private GlacierEQ legal repositories. Validation runs only from `GlacierEQ/public-actions-runner-host` against an exact private commit SHA.

## Registered actions

| Action | Private source | Fixed validation |
|---|---|---|
| `code.monolith.validate-legal-live-reconciliation` | `GlacierEQ/monolith` | Compile, validate registry, run unittest suite |
| `code.monolith.validate-company-engineered-registry` | `GlacierEQ/monolith` | Parse registry JSON, run fixed pytest file |
| `code.casey-legal-mcp.validate-v2` | `GlacierEQ/casey-legal-mcp-server` | Node version, syntax checks, policy tests |

## Security boundary

- Full lowercase 40-character commit SHA required.
- The resolved checkout SHA must equal the requested source SHA.
- One catalog-selected private repository per action.
- Contents-read token only.
- Checkout credential is not exposed to workload code.
- Fixed commands only; callers cannot provide shell commands.
- Specialized adapters do not install packages or download executable dependencies at runtime.
- Runtime dependencies are supplied by the governed public-runner image and fail closed when absent.
- Private workspace is attested before and after execution.
- Tracked source mutation fails the run.
- Detailed receipt stays private; public status is sanitized.
- No case evidence, legal narrative, credential, or private source content is published.

## Activation boundary

The actions depend on the hardened workload-isolation changes in PR #80. Live private checkout and receipt publication remain blocked until the owner configures the dedicated GitHub App client ID and private key required by the public action face. No PAT fallback is permitted.
