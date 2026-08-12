import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateMergeRequest, sha256 } from './merge-authority.mjs';

function legacyRequiredCheckGate(checks, requiredChecks) {
  const normalized = new Map(checks.map((check) => [check?.name, check?.status]));
  return requiredChecks.every((required) => normalized.get(required) === 'pass');
}

function duplicateCheckRequest(statuses) {
  const patch = 'diff --git a/a.txt b/a.txt\n+hello\n';
  const digest = sha256(patch);
  return {
    repository: 'GlacierEQ/example',
    targetBranch: 'main',
    expectedHead: 'abc123',
    intentId: 'evolution-duplicate-check-evidence',
    patch,
    declaredPatchSha256: digest,
    checks: [
      { name: 'unit', status: 'pass' },
      { name: 'security', status: statuses[0] },
      { name: 'security', status: statuses[1] },
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
}

test('candidate improves on canonical last-write-wins duplicate-check baseline', () => {
  const request = duplicateCheckRequest(['fail', 'pass']);
  assert.equal(
    legacyRequiredCheckGate(request.checks, request.policy.requiredChecks),
    true,
    'canonical baseline must reproduce the ambiguous last-write-wins acceptance',
  );

  const candidate = evaluateMergeRequest(request, 'abc123');
  assert.equal(candidate.allowed, false);
  assert.ok(candidate.reasons.includes('duplicate_check:security'));
});

test('candidate decision is order-independent for contradictory duplicate checks', () => {
  for (const statuses of [['fail', 'pass'], ['pass', 'fail']]) {
    const candidate = evaluateMergeRequest(duplicateCheckRequest(statuses), 'abc123');
    assert.equal(candidate.allowed, false);
    assert.ok(candidate.reasons.includes('duplicate_check:security'));
  }
});
