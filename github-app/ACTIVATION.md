# APEX Runner Bridge — Owner Activation

The repository-side GitHub App bridge is complete on branch `apex/github-app-bridge-20260719`.

GitHub requires the account owner to create the App registration, generate its private key, choose the installation repositories, and store the key. Those owner-only settings are not exposed by the connected repository API.

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

The App requires `contents:write` at registration because it must create append-only claims and receipts in the private control repository. Every workload token is down-scoped at runtime to `contents:read` for one exact repository.

## 2. Generate one private key

On the new App settings page, generate a private key and retain the downloaded PEM file securely.

Do not commit the PEM, paste it into an issue, upload it as an artifact, or send it through chat.

## 3. Install on selected repositories only

Install the App on the `GlacierEQ` personal account using **Only select repositories**:

```text
GlacierEQ/mastermind
GlacierEQ/llm-runner-teams
```

Add another private workload only when it is admitted to the APEX action catalog. Do not select `All repositories`.

## 4. Store the App identity on the canonical action face

In `GlacierEQ/public-actions-runner-host` repository settings, create:

```text
Actions variable
APEX_RUNNER_APP_CLIENT_ID = <Client ID shown on the App settings page>

Actions secret
APEX_RUNNER_APP_PRIVATE_KEY = <entire downloaded PEM contents>
```

The secret must include the complete `BEGIN ... PRIVATE KEY` and `END ... PRIVATE KEY` lines.

No App ID or installation ID needs to be stored. The workflow resolves installations dynamically and writes the installation IDs into the private claim and receipt.

## 5. Public visibility gate

`GlacierEQ/public-actions-runner-host` must be public before the canary is run. The repository identity guard intentionally blocks execution while GitHub reports it as private.

## 6. Canary

After the branch is merged, public visibility is confirmed, and the variable/secret exist, dispatch:

```text
Workflow: APEX GitHub App Bridge Canary
job_id: github-app-canary-20260719-001
workload_repo: GlacierEQ/mastermind
workload_ref: main
```

The canary will:

1. verify the canonical public action face;
2. mint a one-repository `contents:write` token for `llm-runner-teams`;
3. mint a one-repository `contents:read` token for `mastermind`;
4. prove each token sees exactly one repository;
5. create `claims/<job_id>.json` privately;
6. checkout and bind the exact private workload commit;
7. create `results/<job_id>.json` privately;
8. revoke both installation tokens automatically when the job ends.

## Acceptance evidence

The App bridge is activated only when all of these are observed:

```text
public workflow conclusion = success
control installation scope = GlacierEQ/llm-runner-teams only
workload installation scope = GlacierEQ/mastermind only
private claim exists before checkout completion
resolved source SHA is 40 lowercase hexadecimal characters
private immutable receipt exists
no PAT fallback used
no private Actions used
```
