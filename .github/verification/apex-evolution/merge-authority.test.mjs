import test from 'node:test';
import assert from 'node:assert/strict';
import { executeMergeAuthorityGraph, evaluateMergeRequest, RESULT, sha256 } from './merge-authority.mjs';

function baseRequest(overrides = {}) {
  const patch = overrides.patch ?? 'diff --git a/a.txt b/a.txt\n+hello\n';
  const digest = sha256(patch);
  const request = {
    repository: 'GlacierEQ/example',
    targetBranch: 'main',
    expectedHead: 'abc123',
    intentId: 'issue-42',
    patch,
    declaredPatchSha256: digest,
    checks: [
      { name: 'unit', status: 'pass' },
      { name: 'security', status: 'pass' },
    ],
    approvals: [{
      actor: 'casey',
      intentId: 'issue-42',
      expectedHead: 'abc123',
      patchSha256: digest,
    }],
    policy: {
      allowedBranches: ['main'],
      requiredChecks: ['unit', 'security'],
      authorizedReviewers: ['casey'],
    },
    ...overrides,
  };
  return request;
}

function harness({
  initialHead = 'abc123',
  mergeSha = 'def456',
  readbackHead,
  priorReceipt,
  throwMerge = false,
  throwHeadReadAt = 0,
  throwPriorLookup = false,
} = {}) {
  let headReads = 0;
  let mergeCalls = 0;
  const receipts = [];
  return {
    receipts,
    get mergeCalls() { return mergeCalls; },
    get headReads() { return headReads; },
    adapters: {
      async getHead() {
        headReads += 1;
        if (throwHeadReadAt === headReads) throw new Error(`head read ${headReads} unavailable`);
        if (headReads === 1) return initialHead;
        return readbackHead ?? mergeSha;
      },
      async getPriorReceipt() {
        if (throwPriorLookup) throw new Error('receipt store unavailable');
        return priorReceipt ?? null;
      },
      async merge() {
        mergeCalls += 1;
        if (throwMerge) throw new Error('provider unavailable');
        return { mergeSha };
      },
      async persistReceipt(receipt) {
        receipts.push(receipt);
      },
    },
  };
}

test('happy path binds intent, checks, approval, expected head and readback', async () => {
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.VERIFIED_COMPLETED);
  assert.equal(receipt.mergeSha, 'def456');
  assert.equal(receipt.readbackHead, 'def456');
  assert.equal(h.mergeCalls, 1);
  assert.equal(h.receipts.length, 1);
});

test('malformed request rejects without provider read or mutation', async () => {
  const h = harness();
  const receipt = await executeMergeAuthorityGraph({}, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('missing_repository'));
  assert.ok(receipt.reasons.includes('missing_policy'));
  assert.equal(h.headReads, 0);
  assert.equal(h.mergeCalls, 0);
  assert.equal(h.receipts.length, 1);
});

test('stale expected head rejects before provider mutation', async () => {
  const h = harness({ initialHead: 'moved999' });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('stale_expected_head'));
  assert.equal(h.mergeCalls, 0);
});

test('initial provider head-read failure is explicit and receipt-backed', async () => {
  const h = harness({ throwHeadReadAt: 1 });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.BLOCKED_PROVIDER_FAILURE);
  assert.ok(receipt.reasons.includes('provider_head_read_failed'));
  assert.equal(receipt.mergeAttempted, false);
  assert.equal(h.mergeCalls, 0);
  assert.equal(h.receipts.length, 1);
});

test('missing required check rejects before provider mutation', async () => {
  const request = baseRequest({ checks: [{ name: 'unit', status: 'pass' }] });
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(request, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('missing_check:security'));
  assert.equal(h.mergeCalls, 0);
});

test('failed required check rejects before provider mutation', async () => {
  const request = baseRequest({ checks: [
    { name: 'unit', status: 'pass' },
    { name: 'security', status: 'fail' },
  ] });
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(request, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('check_not_passed:security'));
  assert.equal(h.mergeCalls, 0);
});

test('duplicate required check evidence rejects regardless of ordering', async () => {
  for (const statuses of [['fail', 'pass'], ['pass', 'fail']]) {
    const request = baseRequest({ checks: [
      { name: 'unit', status: 'pass' },
      { name: 'security', status: statuses[0] },
      { name: 'security', status: statuses[1] },
    ] });
    const h = harness();
    const receipt = await executeMergeAuthorityGraph(request, h.adapters);
    assert.equal(receipt.result, RESULT.REJECTED);
    assert.ok(receipt.reasons.includes('duplicate_check:security'));
    assert.equal(h.headReads, 0);
    assert.equal(h.mergeCalls, 0);
  }
});

test('malformed check evidence rejects before provider access', async () => {
  const cases = [
    { checks: [{ name: 'unit', status: 'pass' }, null], reason: 'malformed_check:1' },
    { checks: [{ name: 'unit', status: 'pass' }, { name: '', status: 'pass' }], reason: 'malformed_check_name:1' },
    { checks: [{ name: 'unit', status: 'pass' }, { name: 'security', status: '' }], reason: 'malformed_check_status:security' },
  ];

  for (const item of cases) {
    const h = harness();
    const receipt = await executeMergeAuthorityGraph(baseRequest({ checks: item.checks }), h.adapters);
    assert.equal(receipt.result, RESULT.REJECTED);
    assert.ok(receipt.reasons.includes(item.reason));
    assert.equal(h.headReads, 0);
    assert.equal(h.mergeCalls, 0);
  }
});

test('approval must be bound to exact patch and head', async () => {
  const request = baseRequest();
  request.approvals[0].patchSha256 = sha256('different patch');
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(request, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('approval_not_bound_to_exact_change'));
  assert.equal(h.mergeCalls, 0);
});

test('unauthorized reviewer cannot authorize the change', async () => {
  const request = baseRequest();
  request.approvals[0].actor = 'mallory';
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(request, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('approval_not_bound_to_exact_change'));
  assert.equal(h.mergeCalls, 0);
});

test('declared patch digest mismatch detects mutation before merge', async () => {
  const request = baseRequest({ declaredPatchSha256: sha256('expected original patch') });
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(request, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('patch_digest_mismatch'));
  assert.equal(h.mergeCalls, 0);
});

test('branch policy prevents unauthorized target mutation', async () => {
  const request = baseRequest({ targetBranch: 'release' });
  const h = harness();
  const receipt = await executeMergeAuthorityGraph(request, h.adapters);
  assert.equal(receipt.result, RESULT.REJECTED);
  assert.ok(receipt.reasons.includes('branch_not_allowed'));
  assert.equal(h.mergeCalls, 0);
});

test('receipt lookup failure blocks mutation and persists a failure receipt', async () => {
  const h = harness({ throwPriorLookup: true });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.BLOCKED_PROVIDER_FAILURE);
  assert.ok(receipt.reasons.includes('provider_receipt_lookup_failed'));
  assert.equal(receipt.mergeAttempted, false);
  assert.equal(h.mergeCalls, 0);
  assert.equal(h.receipts.length, 1);
});

test('idempotent replay never performs the merge twice', async () => {
  const priorReceipt = {
    result: RESULT.VERIFIED_COMPLETED,
    mergeSha: 'prior777',
    readbackHead: 'prior777',
    receiptId: 'receipt-1',
  };
  const h = harness({ priorReceipt });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.DUPLICATE_ALREADY_COMPLETED);
  assert.equal(receipt.priorReceiptId, 'receipt-1');
  assert.equal(h.mergeCalls, 0);
});

test('provider failure is explicit and never reported completed', async () => {
  const h = harness({ throwMerge: true });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.BLOCKED_PROVIDER_FAILURE);
  assert.equal(receipt.mergeAttempted, true);
  assert.equal(receipt.mergeSha, null);
  assert.equal(h.mergeCalls, 1);
});

test('provider response without merge SHA is not completion', async () => {
  const h = harness({ mergeSha: '' });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.BLOCKED_PROVIDER_FAILURE);
  assert.ok(receipt.reasons.includes('provider_did_not_return_merge_sha'));
});

test('post-merge provider readback failure never becomes completion', async () => {
  const h = harness({ throwHeadReadAt: 2 });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.BLOCKED_PROVIDER_FAILURE);
  assert.ok(receipt.reasons.includes('provider_readback_failed'));
  assert.equal(receipt.mergeAttempted, true);
  assert.equal(receipt.mergeSha, 'def456');
  assert.equal(receipt.readbackHead, null);
  assert.equal(h.receipts.length, 1);
});

test('post-merge canonical readback mismatch blocks completion', async () => {
  const h = harness({ mergeSha: 'def456', readbackHead: 'other888' });
  const receipt = await executeMergeAuthorityGraph(baseRequest(), h.adapters);
  assert.equal(receipt.result, RESULT.BLOCKED_READBACK_MISMATCH);
  assert.equal(receipt.mergeAttempted, true);
  assert.equal(receipt.mergeSha, 'def456');
  assert.equal(receipt.readbackHead, 'other888');
});

test('evaluation derives stable patch and idempotency identities', () => {
  const request = baseRequest();
  const first = evaluateMergeRequest(request, 'abc123');
  const second = evaluateMergeRequest(request, 'abc123');
  assert.equal(first.allowed, true);
  assert.equal(first.patchSha256, second.patchSha256);
  assert.equal(first.idempotencyKey, second.idempotencyKey);
});
