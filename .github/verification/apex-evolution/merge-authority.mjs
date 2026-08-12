import crypto from 'node:crypto';

export const RESULT = Object.freeze({
  VERIFIED_COMPLETED: 'VERIFIED_COMPLETED',
  REJECTED: 'REJECTED',
  BLOCKED_PROVIDER_FAILURE: 'BLOCKED_PROVIDER_FAILURE',
  BLOCKED_READBACK_MISMATCH: 'BLOCKED_READBACK_MISMATCH',
  DUPLICATE_ALREADY_COMPLETED: 'DUPLICATE_ALREADY_COMPLETED',
});

export function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

export function stableIdempotencyKey(request, patchSha256) {
  return sha256([
    request.repository,
    request.targetBranch,
    request.expectedHead,
    request.intentId,
    patchSha256,
  ].join(':'));
}

function nonEmptyText(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function normalizeChecks(checks) {
  const statuses = new Map();
  const reasons = [];
  if (!Array.isArray(checks)) return { statuses, reasons };

  checks.forEach((check, index) => {
    if (!check || typeof check !== 'object') {
      reasons.push(`malformed_check:${index}`);
      return;
    }

    if (!nonEmptyText(check.name)) {
      reasons.push(`malformed_check_name:${index}`);
      return;
    }

    const name = check.name.trim();
    if (!nonEmptyText(check.status)) {
      reasons.push(`malformed_check_status:${name}`);
      return;
    }

    if (statuses.has(name)) {
      reasons.push(`duplicate_check:${name}`);
      return;
    }

    statuses.set(name, check.status.trim());
  });

  return { statuses, reasons };
}

function authorizedReviewers(request) {
  return Array.isArray(request?.policy?.authorizedReviewers)
    ? request.policy.authorizedReviewers
    : [];
}

function approvalMatches(approval, request, patchSha256) {
  return approval
    && authorizedReviewers(request).includes(approval.actor)
    && approval.intentId === request.intentId
    && approval.expectedHead === request.expectedHead
    && approval.patchSha256 === patchSha256;
}

function staticReasons(request) {
  const reasons = [];
  for (const field of ['repository', 'targetBranch', 'expectedHead', 'intentId', 'patch']) {
    if (!nonEmptyText(request?.[field])) reasons.push(`missing_${field}`);
  }

  if (!request?.policy || typeof request.policy !== 'object') {
    reasons.push('missing_policy');
  } else {
    if (!Array.isArray(request.policy.allowedBranches)) reasons.push('missing_policy_allowed_branches');
    if (!Array.isArray(request.policy.requiredChecks)) reasons.push('missing_policy_required_checks');
    if (!Array.isArray(request.policy.authorizedReviewers)) reasons.push('missing_policy_authorized_reviewers');
  }
  if (!Array.isArray(request?.checks)) reasons.push('missing_checks');
  if (!Array.isArray(request?.approvals)) reasons.push('missing_approvals');
  return reasons;
}

export function evaluateMergeRequest(request, observedHead, { checkHead = true } = {}) {
  const reasons = staticReasons(request);
  const patchSha256 = nonEmptyText(request?.patch) ? sha256(request.patch) : '';

  if (request?.declaredPatchSha256 && request.declaredPatchSha256 !== patchSha256) {
    reasons.push('patch_digest_mismatch');
  }

  if (Array.isArray(request?.policy?.allowedBranches)
      && !request.policy.allowedBranches.includes(request.targetBranch)) {
    reasons.push('branch_not_allowed');
  }

  if (checkHead && nonEmptyText(request?.expectedHead) && observedHead !== request.expectedHead) {
    reasons.push('stale_expected_head');
  }

  const normalizedChecks = normalizeChecks(request?.checks);
  reasons.push(...normalizedChecks.reasons);
  for (const required of request?.policy?.requiredChecks ?? []) {
    if (!normalizedChecks.statuses.has(required)) reasons.push(`missing_check:${required}`);
    else if (normalizedChecks.statuses.get(required) !== 'pass') {
      reasons.push(`check_not_passed:${required}`);
    }
  }

  const approvals = Array.isArray(request?.approvals) ? request.approvals : [];
  if (!approvals.some((approval) => approvalMatches(approval, request, patchSha256))) {
    reasons.push('approval_not_bound_to_exact_change');
  }

  const uniqueReasons = [...new Set(reasons)];
  return {
    allowed: uniqueReasons.length === 0,
    reasons: uniqueReasons,
    patchSha256,
    idempotencyKey: uniqueReasons.length === 0 ? stableIdempotencyKey(request, patchSha256) : null,
  };
}

function receiptBase(request, evaluation, observedHead) {
  const requiredChecks = Array.isArray(request?.policy?.requiredChecks)
    ? request.policy.requiredChecks
    : [];
  const approvals = Array.isArray(request?.approvals) ? request.approvals : [];
  return {
    schema: 'glaciereq.merge-authority.receipt.v1',
    repository: request?.repository ?? null,
    targetBranch: request?.targetBranch ?? null,
    intentId: request?.intentId ?? null,
    expectedHead: request?.expectedHead ?? null,
    observedHead,
    patchSha256: evaluation.patchSha256,
    idempotencyKey: evaluation.idempotencyKey,
    requiredChecks: [...requiredChecks],
    approvals: approvals.map((approval) => ({
      actor: approval?.actor ?? null,
      intentId: approval?.intentId ?? null,
      expectedHead: approval?.expectedHead ?? null,
      patchSha256: approval?.patchSha256 ?? null,
    })),
  };
}

async function persist(adapters, receipt) {
  await adapters.persistReceipt(receipt);
  return receipt;
}

export async function executeMergeAuthorityGraph(request, adapters) {
  const preflight = evaluateMergeRequest(request, request?.expectedHead ?? null, { checkHead: false });
  if (!preflight.allowed) {
    return persist(adapters, {
      ...receiptBase(request, preflight, null),
      result: RESULT.REJECTED,
      reasons: preflight.reasons,
      mergeAttempted: false,
      mergeSha: null,
      readbackHead: null,
    });
  }

  let prior;
  try {
    prior = await adapters.getPriorReceipt(preflight.idempotencyKey);
  } catch (error) {
    return persist(adapters, {
      ...receiptBase(request, preflight, null),
      result: RESULT.BLOCKED_PROVIDER_FAILURE,
      reasons: ['provider_receipt_lookup_failed'],
      providerError: error instanceof Error ? error.message : String(error),
      mergeAttempted: false,
      mergeSha: null,
      readbackHead: null,
    });
  }

  if (prior?.result === RESULT.VERIFIED_COMPLETED) {
    return persist(adapters, {
      ...receiptBase(request, preflight, null),
      result: RESULT.DUPLICATE_ALREADY_COMPLETED,
      reasons: ['idempotent_replay'],
      mergeAttempted: false,
      mergeSha: prior.mergeSha,
      readbackHead: prior.readbackHead,
      priorReceiptId: prior.receiptId ?? null,
    });
  }

  let observedHead;
  try {
    observedHead = await adapters.getHead(request.repository, request.targetBranch);
  } catch (error) {
    return persist(adapters, {
      ...receiptBase(request, preflight, null),
      result: RESULT.BLOCKED_PROVIDER_FAILURE,
      reasons: ['provider_head_read_failed'],
      providerError: error instanceof Error ? error.message : String(error),
      mergeAttempted: false,
      mergeSha: null,
      readbackHead: null,
    });
  }

  const evaluation = evaluateMergeRequest(request, observedHead);
  const base = receiptBase(request, evaluation, observedHead);

  if (!evaluation.allowed) {
    return persist(adapters, {
      ...base,
      result: RESULT.REJECTED,
      reasons: evaluation.reasons,
      mergeAttempted: false,
      mergeSha: null,
      readbackHead: null,
    });
  }

  let mergeResult;
  try {
    mergeResult = await adapters.merge({
      repository: request.repository,
      targetBranch: request.targetBranch,
      expectedHead: request.expectedHead,
      intentId: request.intentId,
      patch: request.patch,
      patchSha256: evaluation.patchSha256,
      idempotencyKey: evaluation.idempotencyKey,
    });
  } catch (error) {
    return persist(adapters, {
      ...base,
      result: RESULT.BLOCKED_PROVIDER_FAILURE,
      reasons: ['provider_merge_failed'],
      providerError: error instanceof Error ? error.message : String(error),
      mergeAttempted: true,
      mergeSha: null,
      readbackHead: null,
    });
  }

  if (!nonEmptyText(mergeResult?.mergeSha)) {
    return persist(adapters, {
      ...base,
      result: RESULT.BLOCKED_PROVIDER_FAILURE,
      reasons: ['provider_did_not_return_merge_sha'],
      mergeAttempted: true,
      mergeSha: null,
      readbackHead: null,
    });
  }

  let readbackHead;
  try {
    readbackHead = typeof adapters.readbackHead === 'function'
      ? await adapters.readbackHead(request.repository, request.targetBranch, mergeResult.mergeSha)
      : await adapters.getHead(request.repository, request.targetBranch);
  } catch (error) {
    return persist(adapters, {
      ...base,
      result: RESULT.BLOCKED_PROVIDER_FAILURE,
      reasons: ['provider_readback_failed'],
      providerError: error instanceof Error ? error.message : String(error),
      mergeAttempted: true,
      mergeSha: mergeResult.mergeSha,
      readbackHead: null,
    });
  }

  if (readbackHead !== mergeResult.mergeSha) {
    return persist(adapters, {
      ...base,
      result: RESULT.BLOCKED_READBACK_MISMATCH,
      reasons: ['canonical_readback_mismatch'],
      mergeAttempted: true,
      mergeSha: mergeResult.mergeSha,
      readbackHead,
    });
  }

  return persist(adapters, {
    ...base,
    result: RESULT.VERIFIED_COMPLETED,
    reasons: [],
    mergeAttempted: true,
    mergeSha: mergeResult.mergeSha,
    readbackHead,
  });
}
