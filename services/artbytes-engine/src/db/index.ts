import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { env } from '../config/env.js';
import { bigintReplacer } from '../utils/format.js';
import { migrate } from './schema.js';

mkdirSync(dirname(env.app.dbPath), { recursive: true });

export const db = new Database(env.app.dbPath);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');
migrate(db);

function now(): number {
  return Math.floor(Date.now() / 1000);
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ChainName = 'ethereum' | 'base';
export type MintKind =
  | 'seadrop_public'
  | 'seadrop_allowlist'
  | 'custom'
  | 'manual';
export type MintStatus =
  | 'watching'
  | 'approved'
  | 'minting'
  | 'minted'
  | 'failed'
  | 'sold'
  | 'passed';
export type Decision =
  | 'skip'
  | 'watch'
  | 'mint-1'
  | 'mint-conviction'
  | 'max';
export type TxKind = 'mint' | 'approve' | 'list' | 'cancel' | 'sale';
export type TxStatus = 'pending' | 'confirmed' | 'reverted';
export type PositionStatus = 'held' | 'listed' | 'sold';
export type ApprovalKind = 'mint' | 'sell';
export type ApprovalDecision = 'pending' | 'approved' | 'cancelled';
export type LedgerKind = 'commit' | 'spend' | 'refund' | 'proceeds';

export interface Mint {
  id: number;
  slug: string | null;
  name: string | null;
  chain: ChainName;
  contract: string | null;
  mint_kind: MintKind | null;
  price_wei: string | null;
  start_ts: number | null;
  end_ts: number | null;
  max_per_wallet: number | null;
  fee_recipient: string | null;
  wl_wallet: string | null;
  wl_proof: string | null;
  decision: Decision | null;
  qty_target: number | null;
  budget_wei: string | null;
  gas_plan: string | null;
  status: MintStatus;
  exit_plan: string | null;
  notified_t24: 0 | 1;
  created_at: number;
  updated_at: number;
}

export interface Tx {
  id: number;
  mint_id: number;
  kind: TxKind;
  wallet: string | null;
  hash: string | null;
  status: TxStatus;
  value_wei: string | null;
  gas_wei: string | null;
  block: number | null;
  ts: number;
}

export interface Position {
  id: number;
  mint_id: number;
  token_id: string | null;
  wallet: string | null;
  cost_wei: string | null;
  listed_wei: string | null;
  sold_wei: string | null;
  status: PositionStatus;
  ts: number;
}

export interface Approval {
  id: number;
  mint_id: number;
  kind: ApprovalKind;
  requested_at: number;
  decided_at: number | null;
  decision: ApprovalDecision;
  payload: string | null;
}

// ---------------------------------------------------------------------------
// mints
// ---------------------------------------------------------------------------

const insertMintStmt = db.prepare(`
  INSERT INTO mints (slug, name, chain, decision, qty_target, budget_wei, gas_plan, wl_wallet, exit_plan, status, created_at, updated_at)
  VALUES (@slug, @name, @chain, @decision, @qty_target, @budget_wei, @gas_plan, @wl_wallet, @exit_plan, 'watching', @ts, @ts)
`);
const getMintStmt = db.prepare(`SELECT * FROM mints WHERE id = ?`);
const getMintBySlugStmt = db.prepare(`SELECT * FROM mints WHERE slug = ?`);
const updateMintStatusStmt = db.prepare(
  `UPDATE mints SET status = ?, updated_at = ? WHERE id = ?`,
);
const updateMintResolvedStmt = db.prepare(`
  UPDATE mints SET contract = @contract, mint_kind = @mint_kind, price_wei = @price_wei,
    start_ts = @start_ts, end_ts = @end_ts, max_per_wallet = @max_per_wallet,
    fee_recipient = @fee_recipient, name = COALESCE(@name, name), updated_at = @ts
  WHERE id = @id
`);
const setMintBudgetStmt = db.prepare(
  `UPDATE mints SET budget_wei = ?, updated_at = ? WHERE id = ?`,
);
const setT24Stmt = db.prepare(
  `UPDATE mints SET notified_t24 = 1, updated_at = ? WHERE id = ?`,
);
const touchMintStmt = db.prepare(`UPDATE mints SET updated_at = ? WHERE id = ?`);

export interface AddMintInput {
  slug?: string;
  name?: string;
  chain: ChainName;
  decision?: Decision;
  qty_target?: number;
  budget_wei?: bigint;
  gas_plan?: unknown;
  wl_wallet?: string;
  exit_plan?: unknown;
}

export function addMint(input: AddMintInput): Mint {
  const ts = now();
  const info = insertMintStmt.run({
    slug: input.slug ?? null,
    name: input.name ?? null,
    chain: input.chain,
    decision: input.decision ?? 'watch',
    qty_target: input.qty_target ?? 1,
    budget_wei: input.budget_wei != null ? input.budget_wei.toString() : null,
    gas_plan:
      input.gas_plan != null ? JSON.stringify(input.gas_plan, bigintReplacer) : null,
    wl_wallet: input.wl_wallet ?? null,
    exit_plan:
      input.exit_plan != null
        ? JSON.stringify(input.exit_plan, bigintReplacer)
        : null,
    ts,
  });
  return getMint(Number(info.lastInsertRowid))!;
}

export function getMint(id: number): Mint | undefined {
  return getMintStmt.get(id) as Mint | undefined;
}

export function getMintBySlug(slug: string): Mint | undefined {
  return getMintBySlugStmt.get(slug) as Mint | undefined;
}

export function listMintsByStatus(...statuses: MintStatus[]): Mint[] {
  if (statuses.length === 0) return [];
  const placeholders = statuses.map(() => '?').join(',');
  return db
    .prepare(`SELECT * FROM mints WHERE status IN (${placeholders}) ORDER BY start_ts ASC`)
    .all(...statuses) as Mint[];
}

export function listAllMints(): Mint[] {
  return db.prepare(`SELECT * FROM mints ORDER BY created_at DESC`).all() as Mint[];
}

export function updateMintStatus(id: number, status: MintStatus): void {
  updateMintStatusStmt.run(status, now(), id);
}

export interface ResolvedMintData {
  contract: string;
  mint_kind: MintKind;
  price_wei: bigint;
  start_ts: number | null;
  end_ts: number | null;
  max_per_wallet: number | null;
  fee_recipient: string | null;
  name?: string | null;
}

export function updateMintResolved(id: number, r: ResolvedMintData): void {
  updateMintResolvedStmt.run({
    id,
    contract: r.contract,
    mint_kind: r.mint_kind,
    price_wei: r.price_wei.toString(),
    start_ts: r.start_ts,
    end_ts: r.end_ts,
    max_per_wallet: r.max_per_wallet,
    fee_recipient: r.fee_recipient,
    name: r.name ?? null,
    ts: now(),
  });
}

export function setMintBudget(id: number, budgetWei: bigint): void {
  setMintBudgetStmt.run(budgetWei.toString(), now(), id);
}

export function markT24Notified(id: number): void {
  setT24Stmt.run(now(), id);
}

export function touchMint(id: number): void {
  touchMintStmt.run(now(), id);
}

// ---------------------------------------------------------------------------
// txs
// ---------------------------------------------------------------------------

const insertTxStmt = db.prepare(`
  INSERT INTO txs (mint_id, kind, wallet, hash, status, value_wei, gas_wei, block, ts)
  VALUES (@mint_id, @kind, @wallet, @hash, @status, @value_wei, @gas_wei, @block, @ts)
`);
const updateTxStatusStmt = db.prepare(`
  UPDATE txs SET status = @status, gas_wei = COALESCE(@gas_wei, gas_wei),
    block = COALESCE(@block, block) WHERE hash = @hash
`);
const getTxsByMintStmt = db.prepare(`SELECT * FROM txs WHERE mint_id = ? ORDER BY ts ASC`);
const hasMintTxStmt = db.prepare(
  `SELECT COUNT(*) c FROM txs WHERE mint_id = ? AND kind = 'mint' AND status != 'reverted'`,
);

export interface InsertTxInput {
  mint_id: number;
  kind: TxKind;
  wallet?: string;
  hash?: string;
  status: TxStatus;
  value_wei?: bigint;
  gas_wei?: bigint;
  block?: number;
}

export function insertTx(input: InsertTxInput): number {
  const info = insertTxStmt.run({
    mint_id: input.mint_id,
    kind: input.kind,
    wallet: input.wallet ?? null,
    hash: input.hash ?? null,
    status: input.status,
    value_wei: input.value_wei != null ? input.value_wei.toString() : null,
    gas_wei: input.gas_wei != null ? input.gas_wei.toString() : null,
    block: input.block ?? null,
    ts: now(),
  });
  return Number(info.lastInsertRowid);
}

export function updateTxStatus(
  hash: string,
  status: TxStatus,
  extra?: { gas_wei?: bigint; block?: number },
): void {
  updateTxStatusStmt.run({
    hash,
    status,
    gas_wei: extra?.gas_wei != null ? extra.gas_wei.toString() : null,
    block: extra?.block ?? null,
  });
}

export function getTxsByMint(mintId: number): Tx[] {
  return getTxsByMintStmt.all(mintId) as Tx[];
}

/** Idempotence guard: is there already a non-reverted mint TX for this mint? */
export function hasMintTx(mintId: number): boolean {
  const row = hasMintTxStmt.get(mintId) as { c: number };
  return row.c > 0;
}

// ---------------------------------------------------------------------------
// positions
// ---------------------------------------------------------------------------

const insertPositionStmt = db.prepare(`
  INSERT INTO positions (mint_id, token_id, wallet, cost_wei, status, ts)
  VALUES (@mint_id, @token_id, @wallet, @cost_wei, 'held', @ts)
`);
const listHeldStmt = db.prepare(`SELECT * FROM positions WHERE status = 'held' ORDER BY ts ASC`);
const getPositionsByMintStmt = db.prepare(`SELECT * FROM positions WHERE mint_id = ?`);
const setListedStmt = db.prepare(
  `UPDATE positions SET listed_wei = ?, status = 'listed' WHERE id = ?`,
);
const setSoldStmt = db.prepare(
  `UPDATE positions SET sold_wei = ?, status = 'sold' WHERE id = ?`,
);

export interface InsertPositionInput {
  mint_id: number;
  token_id: string;
  wallet: string;
  cost_wei: bigint;
}

export function insertPosition(input: InsertPositionInput): number {
  const info = insertPositionStmt.run({
    mint_id: input.mint_id,
    token_id: input.token_id,
    wallet: input.wallet,
    cost_wei: input.cost_wei.toString(),
    ts: now(),
  });
  return Number(info.lastInsertRowid);
}

export function listHeldPositions(): Position[] {
  return listHeldStmt.all() as Position[];
}

export function getPositionsByMint(mintId: number): Position[] {
  return getPositionsByMintStmt.all(mintId) as Position[];
}

export function setPositionListed(id: number, listedWei: bigint): void {
  setListedStmt.run(listedWei.toString(), id);
}

export function setPositionSold(id: number, soldWei: bigint): void {
  setSoldStmt.run(soldWei.toString(), id);
}

// ---------------------------------------------------------------------------
// approvals
// ---------------------------------------------------------------------------

const insertApprovalStmt = db.prepare(`
  INSERT INTO approvals (mint_id, kind, requested_at, decision, payload)
  VALUES (@mint_id, @kind, @ts, 'pending', @payload)
`);
const decideApprovalStmt = db.prepare(`
  UPDATE approvals SET decision = @decision, decided_at = @ts
  WHERE mint_id = @mint_id AND kind = @kind AND decision = 'pending'
`);
const getPendingApprovalStmt = db.prepare(`
  SELECT * FROM approvals WHERE mint_id = ? AND kind = ? AND decision = 'pending'
  ORDER BY requested_at DESC LIMIT 1
`);
const getApprovalStmt = db.prepare(`SELECT * FROM approvals WHERE id = ?`);
const listPendingStmt = db.prepare(`SELECT * FROM approvals WHERE decision = 'pending' ORDER BY requested_at ASC`);
const expireStaleStmt = db.prepare(`
  UPDATE approvals SET decision = 'cancelled', decided_at = @ts
  WHERE decision = 'pending' AND requested_at < @cutoff
`);

export function createApproval(input: {
  mint_id: number;
  kind: ApprovalKind;
  payload?: unknown;
}): number {
  const info = insertApprovalStmt.run({
    mint_id: input.mint_id,
    kind: input.kind,
    ts: now(),
    payload:
      input.payload != null
        ? JSON.stringify(input.payload, bigintReplacer)
        : null,
  });
  return Number(info.lastInsertRowid);
}

export function decideApproval(
  mintId: number,
  kind: ApprovalKind,
  decision: Exclude<ApprovalDecision, 'pending'>,
): boolean {
  const res = decideApprovalStmt.run({ mint_id: mintId, kind, decision, ts: now() });
  return res.changes > 0;
}

export function getPendingApproval(
  mintId: number,
  kind: ApprovalKind,
): Approval | undefined {
  return getPendingApprovalStmt.get(mintId, kind) as Approval | undefined;
}

export function getApproval(id: number): Approval | undefined {
  return getApprovalStmt.get(id) as Approval | undefined;
}

export function listPendingApprovals(): Approval[] {
  return listPendingStmt.all() as Approval[];
}

/** Cancel approvals older than `cutoffMs` ago. Returns the count expired. */
export function expireStaleApprovals(timeoutMs: number): number {
  const cutoff = now() - Math.floor(timeoutMs / 1000);
  const res = expireStaleStmt.run({ ts: now(), cutoff });
  return res.changes;
}

// ---------------------------------------------------------------------------
// budget_ledger
// ---------------------------------------------------------------------------

const insertLedgerStmt = db.prepare(`
  INSERT INTO budget_ledger (mint_id, wallet, kind, amount_wei, ts, note)
  VALUES (@mint_id, @wallet, @kind, @amount_wei, @ts, @note)
`);
const sumByKindStmt = db.prepare(
  `SELECT amount_wei FROM budget_ledger WHERE kind = ?`,
);
const sumByKindForMintStmt = db.prepare(
  `SELECT amount_wei FROM budget_ledger WHERE kind = ? AND mint_id = ?`,
);

export function addLedger(input: {
  mint_id?: number;
  wallet?: string;
  kind: LedgerKind;
  amount_wei: bigint;
  note?: string;
}): void {
  insertLedgerStmt.run({
    mint_id: input.mint_id ?? null,
    wallet: input.wallet ?? null,
    kind: input.kind,
    amount_wei: input.amount_wei.toString(),
    ts: now(),
    note: input.note ?? null,
  });
}

function sumRows(rows: unknown[]): bigint {
  return (rows as Array<{ amount_wei: string }>).reduce(
    (acc, r) => acc + BigInt(r.amount_wei),
    0n,
  );
}

export function sumByKind(kind: LedgerKind): bigint {
  return sumRows(sumByKindStmt.all(kind));
}

export function sumByKindForMint(kind: LedgerKind, mintId: number): bigint {
  return sumRows(sumByKindForMintStmt.all(kind, mintId));
}
