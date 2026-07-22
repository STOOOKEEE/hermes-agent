import { env } from '../config/env.js';
import {
  createApproval,
  decideApproval,
  getPendingApproval,
  expireStaleApprovals,
  updateMintStatus,
  getMint,
  type Mint,
} from '../db/index.js';
import * as budget from '../budget/index.js';
import { logger } from '../utils/logger.js';
import { weiToEth } from '../utils/format.js';
import type { Notifier } from '../notify/index.js';

const log = logger.child({ src: 'approval' });

/** Is this mint eligible for per-mint auto-approve? (decision='max' + global flag) */
export function isAutoApproveEligible(mint: Mint): boolean {
  return env.approval.autoApprove && mint.decision === 'max';
}

/** Human approved: mark the approval + flip the mint to 'approved'. Notifier-free. */
export function approveMint(mintId: number): boolean {
  decideApproval(mintId, 'mint', 'approved');
  const mint = getMint(mintId);
  if (mint && (mint.status === 'watching' || mint.status === 'approved')) {
    updateMintStatus(mintId, 'approved');
  }
  log.info({ mintId }, 'approved');
  return true;
}

/**
 * Cancel a mint. Before mint: cancel + release any committed budget + mark
 * 'passed'. After a mint TX is sent we cannot cancel the TX (handled by the
 * sell logic). Notifier-free so the CLI can call it directly.
 */
export function cancelMint(mintId: number): boolean {
  decideApproval(mintId, 'mint', 'cancelled');
  const mint = getMint(mintId);
  if (!mint) return false;
  if (mint.status === 'minting' || mint.status === 'minted') {
    log.warn({ mintId }, 'cancel after mint sent — switch to exit logic');
    return false;
  }
  const stillCommitted = budget.committedForMint(mintId);
  if (stillCommitted > 0n) {
    budget.refund(mintId, mint.wl_wallet ?? undefined, stillCommitted, 'cancel');
  }
  updateMintStatus(mintId, 'passed');
  log.info({ mintId }, 'cancelled (budget released)');
  return true;
}

export class ApprovalGate {
  constructor(private notifier: Notifier) {}

  /**
   * Request human approval for a mint (idempotent). Creates a pending approval
   * and notifies once. If the mint is auto-approve eligible, approves immediately.
   * Returns true if already/now approved.
   */
  async request(mint: Mint, opts: { qty: number; costWei: bigint }): Promise<boolean> {
    if (mint.status === 'approved') return true;

    if (isAutoApproveEligible(mint)) {
      log.warn({ mintId: mint.id }, 'auto-approving (decision=max)');
      this.approve(mint.id);
      return true;
    }

    const pending = getPendingApproval(mint.id, 'mint');
    if (!pending) {
      createApproval({
        mint_id: mint.id,
        kind: 'mint',
        payload: { qty: opts.qty, costWei: opts.costWei, costEth: weiToEth(opts.costWei) },
      });
      await this.notifier.approvalRequest(mint, opts);
      log.info({ mintId: mint.id }, 'approval requested');
    }
    return false;
  }

  /** Human approved: mark the approval + flip the mint to 'approved'. */
  approve(mintId: number): boolean {
    return approveMint(mintId);
  }

  /** Cancel a mint (releases committed budget if pre-mint). */
  cancel(mintId: number): boolean {
    return cancelMint(mintId);
  }

  isApproved(mintId: number): boolean {
    const mint = getMint(mintId);
    return mint?.status === 'approved';
  }

  /** Expire pending approvals past the timeout (capital-first default). */
  expireStale(): number {
    const before = expireStaleApprovals(env.approval.timeoutMs);
    if (before > 0) log.info({ count: before }, 'stale approvals expired');
    return before;
  }
}
