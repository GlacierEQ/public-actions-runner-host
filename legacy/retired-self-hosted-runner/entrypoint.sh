#!/usr/bin/env bash
# RETIRED ARCHIVE: Historical self-hosted GitHub Actions runner entrypoint.
# Active workflows run exclusively on GitHub-hosted `ubuntu-latest` runners.

set -euo pipefail

GITHUB_URL="${GITHUB_URL:-https://github.com/GlacierEQ}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_NAME="${RUNNER_NAME:-legal-brief-runner-$(hostname)}"
RUNNER_LABELS="${RUNNER_LABELS:-legal,latex,hawaii}"

if [ -z "$RUNNER_TOKEN" ]; then
  echo "ERROR: RUNNER_TOKEN env var is required" >&2
  exit 1
fi

cd /home/runner
RUNNER_WORKDIR="$(mktemp -d /tmp/apex-ephemeral-work.XXXXXX)"

cleanup() {
  echo "Tearing down ephemeral runner and workspace..."
  ./config.sh remove --token "$RUNNER_TOKEN" || true
  rm -rf "$RUNNER_WORKDIR" /home/runner/_work /home/runner/.runner /home/runner/.credentials*
}
trap cleanup EXIT

./config.sh \
  --url "$GITHUB_URL" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --work "$RUNNER_WORKDIR" \
  --ephemeral \
  --unattended \
  --replace

env -u RUNNER_TOKEN ./run.sh
