# APEX Runner Bridge — Automated Owner Bootstrap

The bridge must not depend on a human copying a Client ID, downloading a PEM, or pasting that PEM into repository settings.

## Canonical ignition

On Windows, clone or download this repository and double-click:

```text
START_APEX_RUNNER_BRIDGE.cmd
```

The launcher verifies GitHub CLI and Python, opens browser authentication only when necessary, loads the pinned activation target from `github-app/activation-target.json`, and then invokes the hardened bootstrap through `github-app/start_apex_runner_bridge.ps1`.

The cross-platform command behind the launcher is:

```bash
python github-app/bootstrap_apex_github_app.py --run-id <pinned-workflow-run-id>
```

Run it only from an authenticated desktop-agent session with GitHub CLI available. The process opens GitHub's owner-consent screens in the controlled browser and performs the credential plumbing itself.

## Current activation target

The owning control plane pins the exact failed workflow that must resume after App activation in `github-app/activation-target.json`. The launcher refuses to run if that target is missing, malformed, references a non-APEX workflow, or does not bind a full lowercase source commit SHA.

Current target:

```text
target_id:        estate-monolith-legal-live-20260807
workflow_run_id:  31170532956
issue:            #95
action:           code.monolith.validate-legal-live-reconciliation
source_repo:      GlacierEQ/monolith
source_ref:       f686505aa521faf5f9511e423124832ad08aae3a
```

Do not create a replacement job envelope merely because activation was blocked. After owner consent and exact installation complete, rerun the pinned failed workflow so the immutable source identity remains unchanged.

## What the bootstrap performs

1. Loads and validates `github-app/app-manifest.json`.
2. Starts a callback server fixed to `127.0.0.1` and rejects an unexpected `Host` header.
3. Opens GitHub's App Manifest registration flow.
4. Validates the returned anti-forgery `state` value.
5. Exchanges the one-time manifest code for the App configuration.
6. Keeps the generated PEM in process memory only; Python cannot guarantee byte-for-byte memory zeroization.
7. Records the current Client ID variable so a failed secret write can roll back cleanly.
8. Writes the App Client ID to `APEX_RUNNER_APP_CLIENT_ID`.
9. Pipes the PEM through standard input directly into `APEX_RUNNER_APP_PRIVATE_KEY`.
10. Discards application-response references to the PEM, client secret, and webhook secret before reporting.
11. Opens the App installation screen.
12. Polls GitHub until the installation contains exactly the approved repositories.
13. Rejects any unexpected repository access.
14. Reruns only the failed jobs in the workflow pinned by `github-app/activation-target.json`.
15. Waits for a new workflow attempt rather than accepting the pre-rerun result.
16. Requires every named completion record to exist and conclude `success` before returning success.

The PEM is never printed, placed in a command-line argument, written to disk, committed, uploaded as an artifact, or transported through chat.

## Fixed App contract

| Setting | Required value |
|---|---|
| Owner | `GlacierEQ` personal account |
| Name | `APEX Runner Bridge` |
| Public app | **No** |
| Webhooks | **Inactive** |
| Repository permissions — Contents | **Read and write** |
| Every other repository permission | **No access** |
| Organization permissions | **No access** |
| Account permissions | **No access** |
| OAuth authorization on install | **Off** |

GitHub supplies read-only Metadata access automatically when required.

## Exact installation allowlist

```text
GlacierEQ/mastermind
GlacierEQ/llm-runner-teams
GlacierEQ/monolith
GlacierEQ/MEGA-PDF
```

The bootstrap fails closed when the observed installation contains any repository outside this set or omits an approved repository after the bounded installation window.

## Human boundary

GitHub may require the signed-in account owner to approve the App creation or installation screen. That is an account-consent boundary, not a credential-transport task. The desktop agent handles authentication, credential exchange, repository settings, installation verification, rerun, and reporting. The owner does not generate, view, copy, paste, store, or transmit the key.

## Verification

```bash
python -m pytest -q tests/test_github_app_manifest_bootstrap.py tests/test_apex_windows_launcher.py
python -m py_compile github-app/bootstrap_apex_github_app.py
```

Tests enforce:

- private owner-only App configuration;
- inactive webhooks and no events;
- `contents:write` as the only declared permission;
- anti-forgery state separation;
- loopback-only callback binding;
- bounded GitHub CLI execution;
- exact four-repository installation allowlist;
- PEM injection through standard input only;
- rollback after a partial credential write;
- new-attempt detection after rerun;
- rejection of missing, skipped, or failed completion records;
- a real Windows double-click entrypoint;
- pinned live activation-target loading and immutable source validation;
- browser consent as the only human interaction;
- no manual private-key prompt, display, or transport path.

## Completion condition

The bridge is complete only when the new rerun records every item below as `success`:

```text
Require GitHub App bridge configuration
Mint one-repository private control token
Assert private non-executing control plane
Atomically claim immutable job ID
Verify private dual-confirmation record
Mint one-repository private workload token
Checkout catalog-approved workload
Bind exact workload repository and commit
Execute isolated public action adapter
Verify post-run control, workload, and result integrity
Return verified detailed result to private control plane
Publish truthful sanitized issue status
workflow conclusion
```

No PAT fallback is permitted. Canonical legal promotion remains blocked until the bounded private receipt is independently verified.
