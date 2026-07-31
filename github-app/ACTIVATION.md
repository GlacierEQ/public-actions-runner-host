# APEX Runner Bridge — Owner Activation

The repository-side bridge, MEGA-PDF relay, security checks, and observable canary are installed. GitHub requires the personal-account owner to create the App registration, generate its private key, choose installation repositories, and store the key. Those owner-only settings are not exposed by the connected repository API.

## 1. Register the private App

Use the prefilled owner registration page:

[Create APEX Runner Bridge](https://github.com/settings/apps/new?name=APEX%20Runner%20Bridge&description=Owner-only%20short-lived%20least-privilege%20bridge%20for%20the%20APEX%20public%20action%20face.&url=https%3A%2F%2Fgithub.com%2FGlacierEQ%2Fpublic-actions-runner-host&public=false&contents=write&webhook_active=false)

Confirm this exact configuration before creating it:

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

Contents write is registered because the bridge writes bounded receipts to explicitly selected private repositories. Each runtime token is down-scoped to one exact repository and expires automatically.

## 2. Generate one private key

On the new App settings page, generate a private key and retain the downloaded PEM file securely.

Do not commit the PEM, paste it into an issue, upload it as an artifact, or send it through chat.

## 3. Install on selected repositories only

Install the App on the `GlacierEQ` personal account using **Only select repositories**:

```text
GlacierEQ/mastermind
GlacierEQ/llm-runner-teams
GlacierEQ/monolith
GlacierEQ/MEGA-PDF
```

Do not select `All repositories`.

For MEGA-PDF, the token is limited at runtime to `GlacierEQ/MEGA-PDF` with `contents:write`. The relay reads the selected source ref and writes generated inventory artifacts only to:

```text
automation/mega-pdf-function-genome-results
```

No private inventory artifact is uploaded to the public action-face repository.

## 4. Store the App identity on the canonical action face

In `GlacierEQ/public-actions-runner-host` repository settings, create exactly:

```text
Actions variable
APEX_RUNNER_APP_CLIENT_ID = <Client ID shown on the App settings page>

Actions secret
APEX_RUNNER_APP_PRIVATE_KEY = <entire downloaded PEM contents>
```

The secret must include the complete `BEGIN ... PRIVATE KEY` and `END ... PRIVATE KEY` lines.

No App ID or installation ID needs to be stored. The workflow resolves installations dynamically. Static PAT fallback is prohibited.

## 5. Public visibility gate

`GlacierEQ/public-actions-runner-host` must remain public. The identity guard blocks execution if GitHub reports any other visibility or repository identity.

## 6. MEGA-PDF activation run

After the variable and secret exist, rerun PR:

```text
GlacierEQ/public-actions-runner-host #67
Workflow: MEGA-PDF Private Relay PR Trigger
Source: GlacierEQ/MEGA-PDF
Ref: upgrade/mega-pdf-document-intelligence-v2
```

The relay will:

1. verify the public action-face identity;
2. mint one short-lived token scoped only to `GlacierEQ/MEGA-PDF`;
3. checkout and bind the exact private source commit;
4. compile the governed control plane;
5. run the focused governance and ingestion tests;
6. execute the real monorepo Function Genome ingestion;
7. verify every receipt, chain link, terminal root, and promotion invariant;
8. publish the private artifacts to `automation/mega-pdf-function-genome-results`;
9. revoke the installation token automatically at job completion.

## Acceptance evidence

The bridge is activated for MEGA-PDF only when all of these are observed:

```text
public relay workflow conclusion = success
exact private source SHA recorded
installation token scope = GlacierEQ/MEGA-PDF only
contents permission = write; all unrelated permissions absent
focused tests pass
receipt_chain_valid = true
discovered = promoted_to_probed + blocked
receipts = discovered
approved = 0
defaults_promoted = 0
private results branch exists
no PAT fallback used
no private Actions minutes used
no private inventory artifact published publicly
```
