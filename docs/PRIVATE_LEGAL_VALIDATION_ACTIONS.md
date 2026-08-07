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

## Foundation state

The workload-isolation and immutable-source controls from public-runner PR #80 are merged into `main`. These validation actions therefore target the merged security boundary rather than an unmerged architecture branch.

## Activation boundary

The public action face uses the repository's governed GitHub App manifest/bootstrap path. Manual private-key handling and PAT fallback are not part of the production contract.

A validation action may execute only when the runtime App installation actually includes its target repository and can mint the required read-scoped token. App existence or successful runner self-verification does not imply access to every private repository; missing installation scope fails closed.

### Current observed scope checkpoint — 2026-08-07

Repository-native verification reported manifest-based provisioning with `manual_credential_handling: false`. The verified installation set included `GlacierEQ/monolith` but did not include `GlacierEQ/casey-legal-mcp-server` at that checkpoint. Therefore the Monolith validation actions may proceed to live execution testing, while `code.casey-legal-mcp.validate-v2` remains blocked until installation scope for its exact source repository is proven.
