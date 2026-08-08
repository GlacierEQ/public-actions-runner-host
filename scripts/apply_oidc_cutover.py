#!/usr/bin/env python3
"""One-shot deterministic cutover from repository-stored App keys to Keymaster OIDC."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    workflow = ".github/workflows/apex-pillar-runner.yml"
    replace_once(
        workflow,
        "permissions:\n  contents: read\n  issues: write\n",
        "permissions:\n  id-token: write\n  contents: read\n  issues: write\n",
    )
    replace_once(
        workflow,
        """      - name: Require GitHub App bridge configuration
        if: steps.plan.outcome == 'success'
        id: app_config
        continue-on-error: true
        env:
          APP_CLIENT_ID: ${{ vars.APEX_RUNNER_APP_CLIENT_ID }}
          APP_PRIVATE_KEY: ${{ secrets.APEX_RUNNER_APP_PRIVATE_KEY }}
        run: |
          test -n "$APP_CLIENT_ID" || { echo "APEX_RUNNER_APP_CLIENT_ID repository variable is required" >&2; exit 1; }
          test -n "$APP_PRIVATE_KEY" || { echo "APEX_RUNNER_APP_PRIVATE_KEY repository secret is required" >&2; exit 1; }
          case "$APP_PRIVATE_KEY" in
            *"BEGIN "*"PRIVATE KEY"*) ;;
            *) echo "APEX_RUNNER_APP_PRIVATE_KEY does not contain a PEM private key" >&2; exit 1 ;;
          esac
""",
        """      - name: Require GitHub OIDC Keymaster bridge
        if: steps.plan.outcome == 'success'
        id: app_config
        continue-on-error: true
        run: |
          test -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" || { echo "GitHub OIDC request URL unavailable" >&2; exit 1; }
          test -n "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" || { echo "GitHub OIDC request token unavailable" >&2; exit 1; }
          python3 -m py_compile scripts/keymaster_oidc_token.py
""",
    )
    replace_once(
        workflow,
        """      - name: Mint one-repository private control token
        if: steps.plan.outcome == 'success' && steps.app_config.outcome == 'success'
        id: control_token
        continue-on-error: true
        uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        with:
          client-id: ${{ vars.APEX_RUNNER_APP_CLIENT_ID }}
          private-key: ${{ secrets.APEX_RUNNER_APP_PRIVATE_KEY }}
          owner: GlacierEQ
          repositories: GlacierEQ/llm-runner-teams
          permission-contents: write
""",
        """      - name: Mint one-repository private control token
        if: steps.plan.outcome == 'success' && steps.app_config.outcome == 'success'
        id: control_token
        continue-on-error: true
        run: |
          python3 scripts/keymaster_oidc_token.py \\
            --repository GlacierEQ/llm-runner-teams \\
            --permission contents=write \\
            --operation public-action-control
""",
    )
    replace_once(
        workflow,
        """      - name: Mint one-repository private workload token
        if: >-
          steps.plan.outcome == 'success' &&
          steps.replay.outcome == 'success' &&
          ((steps.plan.outputs.pillar != 'G' && steps.plan.outputs.pillar != 'I') ||
           steps.approval.outcome == 'success')
        id: workload_token
        continue-on-error: true
        uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        with:
          client-id: ${{ vars.APEX_RUNNER_APP_CLIENT_ID }}
          private-key: ${{ secrets.APEX_RUNNER_APP_PRIVATE_KEY }}
          owner: GlacierEQ
          repositories: ${{ steps.plan.outputs.source_repo }}
          permission-contents: read
""",
        """      - name: Mint one-repository private workload token
        if: >-
          steps.plan.outcome == 'success' &&
          steps.replay.outcome == 'success' &&
          ((steps.plan.outputs.pillar != 'G' && steps.plan.outputs.pillar != 'I') ||
           steps.approval.outcome == 'success')
        id: workload_token
        continue-on-error: true
        run: |
          python3 scripts/keymaster_oidc_token.py \\
            --repository "${{ steps.plan.outputs.source_repo }}" \\
            --permission contents=read \\
            --operation public-action-workload
""",
    )

    selftest = "scripts/action_face_selftest.py"
    replace_once(
        selftest,
        """        "permission-contents: read",
        "permission-contents: write",
        "persist-credentials: false",
        "steps.synthesize.outputs.synthesized == 'true'",
        CHECKOUT_PIN,
        APP_TOKEN_PIN,
""",
        """        "id-token: write",
        "scripts/keymaster_oidc_token.py",
        "--permission contents=read",
        "--permission contents=write",
        "--repository GlacierEQ/llm-runner-teams",
        "persist-credentials: false",
        "steps.synthesize.outputs.synthesized == 'true'",
        CHECKOUT_PIN,
""",
    )
    replace_once(
        selftest,
        """        "actions/create-github-app-token@v",
        "AKOS_POLICY_SHA256: ${{ secrets.AKOS_POLICY_SHA256 }}",
""",
        """        "actions/create-github-app-token@v",
        "actions/create-github-app-token@",
        "secrets.APEX_RUNNER_APP_PRIVATE_KEY",
        "vars.APEX_RUNNER_APP_CLIENT_ID",
        "AKOS_POLICY_SHA256: ${{ secrets.AKOS_POLICY_SHA256 }}",
""",
    )

    ingress = "tests/test_apex_job_ingress_exclusivity.py"
    replace_once(
        ingress,
        """    assert "permissions:\\n  contents: read\\n  issues: write" in text
    assert "Mint one-repository private control token" in text
    assert "repositories: GlacierEQ/llm-runner-teams" in text
    assert "permission-contents: write" in text
    assert "Mint one-repository private workload token" in text
    assert "repositories: ${{ steps.plan.outputs.source_repo }}" in text
    assert "permission-contents: read" in text
    assert text.count("persist-credentials: false") >= 3
    assert "APEX_RUNNER_APP_CLIENT_ID" in text
    assert "APEX_RUNNER_APP_PRIVATE_KEY" in text
""",
        """    assert "permissions:\\n  id-token: write\\n  contents: read\\n  issues: write" in text
    assert "Mint one-repository private control token" in text
    assert "--repository GlacierEQ/llm-runner-teams" in text
    assert "--permission contents=write" in text
    assert "Mint one-repository private workload token" in text
    assert '--repository "${{ steps.plan.outputs.source_repo }}"' in text
    assert "--permission contents=read" in text
    assert text.count("persist-credentials: false") >= 3
    assert "APEX_RUNNER_APP_CLIENT_ID" not in text
    assert "APEX_RUNNER_APP_PRIVATE_KEY" not in text
    assert "scripts/keymaster_oidc_token.py" in text
""",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
