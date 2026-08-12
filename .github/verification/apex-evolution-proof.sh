#!/usr/bin/env bash
set -euo pipefail

ROOT='.github/verification/apex-evolution'
SOURCE="$ROOT/merge-authority.mjs"
TESTS="$ROOT/merge-authority.test.mjs"
BENCHMARK="$ROOT/evolution-benchmark.test.mjs"
PRIVATE_CANDIDATE_HEAD='40ad6c1f24c8f51c06eb359657ba5cad4fdcac2b'
BASELINE_HEAD='f791c85a81768e72446619b39b5312ef1c768a02'
EXPECTED_SOURCE_BLOB='b0634ee0f39ff5ef636a806a6d967385c614330d'
EXPECTED_TEST_BLOB='5db04e65163f183c1e906ac4a505ba9c73345a2e'
EXPECTED_BENCHMARK_BLOB='16b68535908a0e04850a7afaf5979bfdc5038754'

assert_blob() {
  local file="$1"
  local expected="$2"
  local actual
  actual="$(git hash-object "$file")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Git blob mismatch for $file: expected $expected got $actual" >&2
    exit 1
  fi
}

assert_blob "$SOURCE" "$EXPECTED_SOURCE_BLOB"
assert_blob "$TESTS" "$EXPECTED_TEST_BLOB"
assert_blob "$BENCHMARK" "$EXPECTED_BENCHMARK_BLOB"

node --check "$SOURCE"
node --check "$TESTS"
node --check "$BENCHMARK"

node --test "$TESTS" "$BENCHMARK" | tee .verification-artifacts/apex-evolution-tests.tap

test_count="$(awk '/^# tests / {print $3}' .verification-artifacts/apex-evolution-tests.tap | tail -1)"
pass_count="$(awk '/^# pass / {print $3}' .verification-artifacts/apex-evolution-tests.tap | tail -1)"
fail_count="$(awk '/^# fail / {print $3}' .verification-artifacts/apex-evolution-tests.tap | tail -1)"
[[ -n "$test_count" && "$test_count" = "$pass_count" && "$fail_count" = '0' ]]

node --input-type=module <<'NODE' > .verification-artifacts/apex-evolution-comparison.json
import { evaluateMergeRequest, sha256 } from './.github/verification/apex-evolution/merge-authority.mjs';

const patch = 'diff --git a/a.txt b/a.txt\n+hello\n';
const digest = sha256(patch);
const request = {
  repository: 'GlacierEQ/example',
  targetBranch: 'main',
  expectedHead: 'abc123',
  intentId: 'evolution-duplicate-check-evidence',
  patch,
  declaredPatchSha256: digest,
  checks: [
    { name: 'unit', status: 'pass' },
    { name: 'security', status: 'fail' },
    { name: 'security', status: 'pass' },
  ],
  approvals: [{
    actor: 'casey',
    intentId: 'evolution-duplicate-check-evidence',
    expectedHead: 'abc123',
    patchSha256: digest,
  }],
  policy: {
    allowedBranches: ['main'],
    requiredChecks: ['unit', 'security'],
    authorizedReviewers: ['casey'],
  },
};

const legacy = new Map(request.checks.map((check) => [check?.name, check?.status]));
const baselineAllowed = request.policy.requiredChecks.every((required) => legacy.get(required) === 'pass');
const candidate = evaluateMergeRequest(request, 'abc123');
const result = {
  baseline_allowed: baselineAllowed,
  candidate_allowed: candidate.allowed,
  candidate_reasons: candidate.reasons,
  winner: baselineAllowed && !candidate.allowed && candidate.reasons.includes('duplicate_check:security')
    ? 'candidate'
    : 'none',
};
if (result.winner !== 'candidate') process.exitCode = 1;
console.log(JSON.stringify(result, null, 2));
NODE

node - <<'NODE'
const fs = require('node:fs');
const cp = require('node:child_process');
const comparison = JSON.parse(fs.readFileSync('.verification-artifacts/apex-evolution-comparison.json', 'utf8'));
const tap = fs.readFileSync('.verification-artifacts/apex-evolution-tests.tap', 'utf8');
const number = (label) => Number(tap.match(new RegExp(`^# ${label} (\\d+)$`, 'm'))?.[1] ?? -1);
const receipt = {
  schema: 'glaciereq.apex-evolution-public-proof.v1',
  status: comparison.winner === 'candidate' && number('fail') === 0 ? 'PASS' : 'FAIL',
  repository: 'GlacierEQ/apex-github-worker',
  capability: 'merge_authority_graph',
  baseline_head: 'f791c85a81768e72446619b39b5312ef1c768a02',
  private_candidate_head: '40ad6c1f24c8f51c06eb359657ba5cad4fdcac2b',
  public_proof_host: 'GlacierEQ/public-actions-runner-host',
  public_proof_host_head: process.env.GITHUB_SHA || cp.execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  exact_git_blobs: {
    'merge-authority.mjs': 'b0634ee0f39ff5ef636a806a6d967385c614330d',
    'merge-authority.test.mjs': '5db04e65163f183c1e906ac4a505ba9c73345a2e',
    'evolution-benchmark.test.mjs': '16b68535908a0e04850a7afaf5979bfdc5038754',
  },
  tests: { total: number('tests'), passed: number('pass'), failed: number('fail') },
  comparison,
  authority_boundary: {
    private_source_mutated: false,
    provider_mutation_exercised: false,
    github_adoption_claimed: false,
    production_scale_reliability_claimed: false,
  },
};
fs.writeFileSync('.verification-artifacts/apex-evolution-proof.json', JSON.stringify(receipt, null, 2) + '\n');
if (receipt.status !== 'PASS') process.exit(1);
console.log(JSON.stringify(receipt, null, 2));
NODE
