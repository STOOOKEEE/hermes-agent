import { db, addLedger, sumByKind, sumByKindForMint } from '../db/index.js';
import { logger } from '../utils/logger.js';
import { weiToEth } from '../utils/format.js';

const log = logger.child({ src: 'budget' });

/**
 * Thrown when a reservation would breach the per-mint budget. The engine no
 * longer carries a hard-coded global cap — the AI (Hermes) sets `budget_wei`
 * per mint in the order, and that is the only spending guard.
 */
export class BudgetExceededError extends Error {
  constructor(
    readonly requestedWei: bigint,
    readonly mintBudgetWei: bigint,
  ) {
    super(
      `Per-mint budget exceeded: cost ${weiToEth(requestedWei)} ETH > budget ` +
        `${weiToEth(mintBudgetWei)} ETH`,
    );
    this.name = 'BudgetExceededError';
  }
}

/** Thrown when a mint has no AI-assigned budget (capital-first: never spend). */
export class NoBudgetError extends Error {
  constructor(mintId: number) {
    super(`Mint #${mintId} has no budget_wei set — refusing (capital-first)`);
    this.name = 'NoBudgetError';
  }
}

// ---- Ledger accounting (no global cap) ----

/** Total committed across all mints (Σcommit − Σrefund) — accounting only. */
export function committed(): bigint {
  return sumByKind('commit') - sumByKind('refund');
}

export function spent(): bigint {
  return sumByKind('spend');
}

/** proceeds are informational only — they never free budget (capital-first). */
export function proceeds(): bigint {
  return sumByKind('proceeds');
}

/** Outstanding committed (not yet refunded) amount for a single mint. */
export function committedForMint(mintId: number): bigint {
  return sumByKindForMint('commit', mintId) - sumByKindForMint('refund', mintId);
}

export function spentForMint(mintId: number): bigint {
  return sumByKindForMint('spend', mintId);
}

/** Dry check: would `costWei` fit within this mint's AI-assigned budget? */
export function check(mintId: number, costWei: bigint, mintBudgetWei: bigint): boolean {
  return committedForMint(mintId) + costWei <= mintBudgetWei;
}

/**
 * Atomically reserve `costWei` against THIS mint's budget. Read + write run in
 * a single synchronous SQLite transaction; because better-sqlite3 is
 * synchronous and Node is single-threaded, nothing interleaves, so the per-mint
 * budget cannot be breached. Throws (and writes nothing) on overflow.
 */
const reserveTxn = db.transaction(
  (mintId: number, wallet: string | undefined, costWei: bigint, mintBudgetWei: bigint, note?: string) => {
    if (committedForMint(mintId) + costWei > mintBudgetWei) {
      throw new BudgetExceededError(costWei, mintBudgetWei);
    }
    addLedger({ mint_id: mintId, wallet, kind: 'commit', amount_wei: costWei, note });
  },
);

export function reserve(
  mintId: number,
  wallet: string | undefined,
  costWei: bigint,
  mintBudgetWei: bigint,
  note?: string,
): void {
  reserveTxn(mintId, wallet, costWei, mintBudgetWei, note);
  log.info(
    { mintId, wallet, cost: weiToEth(costWei), budget: weiToEth(mintBudgetWei) },
    'budget reserved',
  );
}

/** Record actual spend (after the TX confirms). */
export function spend(
  mintId: number | undefined,
  wallet: string | undefined,
  amountWei: bigint,
  note?: string,
): void {
  addLedger({ mint_id: mintId, wallet, kind: 'spend', amount_wei: amountWei, note });
  log.info({ mintId, wallet, amount: weiToEth(amountWei) }, 'budget spent');
}

/** Release a previously committed amount (e.g. unused reservation or abort). */
export function refund(
  mintId: number | undefined,
  wallet: string | undefined,
  amountWei: bigint,
  note?: string,
): void {
  if (amountWei <= 0n) return;
  addLedger({ mint_id: mintId, wallet, kind: 'refund', amount_wei: amountWei, note });
  log.info({ mintId, wallet, amount: weiToEth(amountWei) }, 'budget refunded');
}

/** Record sale proceeds (informational PnL — does not free budget). */
export function recordProceeds(
  mintId: number | undefined,
  wallet: string | undefined,
  amountWei: bigint,
  note?: string,
): void {
  addLedger({ mint_id: mintId, wallet, kind: 'proceeds', amount_wei: amountWei, note });
}

export interface BudgetSnapshot {
  committedWei: bigint;
  spentWei: bigint;
  proceedsWei: bigint;
}

export function snapshot(): BudgetSnapshot {
  return {
    committedWei: committed(),
    spentWei: spent(),
    proceedsWei: proceeds(),
  };
}
