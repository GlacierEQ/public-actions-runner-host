#!/bin/bash
# Docker entrypoint for self-hosted GitHub Actions runner
# Usage: docker run -e GITHUB_URL=... -e RUNNER_TOKEN=... glaciereq/legal-runner

set -e

GITHUB_URL=${GITHUB_URL:-"https://github.com/GlacierEQ"}
RUNNER_TOKEN=${RUNNER_TOKEN:-""}
RUNNER_NAME=${RUNNER_NAME:-"legal-brief-runner-$(hostname)"}
RUNNER_LABELS=${RUNNER_LABELS:-"legal,latex,hawaii"}

if [ -z "$RUNNER_TOKEN" ]; then
  echo "ERROR: RUNNER_TOKEN env var is required"
  echo "Get it from: https://github.com/GlacierEQ/public-actions-runner-host/settings/actions/runners/new"
  exit 1
fi

cd /home/runner

./config.sh \
  --url "$GITHUB_URL" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "$RUNNER_LABELS" \
  --unattended \
  --replace

cleanup() {
  echo "Removing runner..."
  ./config.sh remove --token "$RUNNER_TOKEN"
}
trap cleanup EXIT

./run.sh
