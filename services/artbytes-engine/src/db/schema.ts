import type Database from 'better-sqlite3';

/**
 * Schema for the artbytes-engine. Money amounts are stored as TEXT (wei, a
 * stringified bigint) — never as numbers (wei overflow Number.MAX_SAFE_INTEGER).
 * Timestamps are unix seconds (INTEGER) since we juggle on-chain start_ts.
 */
export function migrate(db: Database.Database): void {
  db.exec(`
    -- a project/mint being tracked
    CREATE TABLE IF NOT EXISTS mints (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      slug          TEXT UNIQUE,
      name          TEXT,
      chain         TEXT NOT NULL,              -- 'ethereum' | 'base'
      contract      TEXT,                       -- 0x... (resolved)
      mint_kind     TEXT,                       -- 'seadrop_public' | 'seadrop_allowlist' | 'custom' | 'manual'
      price_wei     TEXT,                       -- unit price (string bigint)
      start_ts      INTEGER,                    -- mint start (unix seconds)
      end_ts        INTEGER,                    -- mint end (unix seconds)
      max_per_wallet INTEGER,                   -- maxTotalMintableByWallet from the drop
      fee_recipient TEXT,                       -- SeaDrop fee recipient
      wl_wallet     TEXT,                       -- 'W_raid' | 'W_krysko' | null
      wl_proof      TEXT,                       -- JSON allowlist proof if needed
      decision      TEXT,                       -- 'skip'|'watch'|'mint-1'|'mint-conviction'|'max'
      qty_target    INTEGER,                    -- units to mint
      budget_wei    TEXT,                       -- budget allocated to this mint (AI-defined)
      gas_plan      TEXT,                       -- JSON: {priorityGwei, baseFeeMult, maxGasEth} (AI override, optional)
      status        TEXT NOT NULL DEFAULT 'watching', -- watching|approved|minting|minted|failed|sold|passed
      exit_plan     TEXT,                       -- JSON: {flipPct, derisk, moonbag}
      notified_t24  INTEGER NOT NULL DEFAULT 0, -- T-24h notification sent?
      created_at    INTEGER NOT NULL,
      updated_at    INTEGER NOT NULL
    );

    -- each on-chain transaction
    CREATE TABLE IF NOT EXISTS txs (
      id        INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id   INTEGER NOT NULL,
      kind      TEXT NOT NULL,                  -- 'mint'|'approve'|'list'|'cancel'|'sale'
      wallet    TEXT,
      hash      TEXT,
      status    TEXT NOT NULL,                  -- pending|confirmed|reverted
      value_wei TEXT,
      gas_wei   TEXT,
      block     INTEGER,
      ts        INTEGER NOT NULL,
      FOREIGN KEY (mint_id) REFERENCES mints(id)
    );

    -- post-mint position (inventory held)
    CREATE TABLE IF NOT EXISTS positions (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id    INTEGER NOT NULL,
      token_id   TEXT,
      wallet     TEXT,
      cost_wei   TEXT,
      listed_wei TEXT,
      sold_wei   TEXT,
      status     TEXT NOT NULL DEFAULT 'held',  -- held|listed|sold
      ts         INTEGER NOT NULL,
      FOREIGN KEY (mint_id) REFERENCES mints(id)
    );

    -- human approvals
    CREATE TABLE IF NOT EXISTS approvals (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id      INTEGER NOT NULL,
      kind         TEXT NOT NULL,               -- 'mint'|'sell'
      requested_at INTEGER NOT NULL,
      decided_at   INTEGER,
      decision     TEXT NOT NULL DEFAULT 'pending', -- pending|approved|cancelled
      payload      TEXT,                        -- details shown to the human
      FOREIGN KEY (mint_id) REFERENCES mints(id)
    );

    -- budget ledger (single source of truth for the cap)
    CREATE TABLE IF NOT EXISTS budget_ledger (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id    INTEGER,
      wallet     TEXT,
      kind       TEXT NOT NULL,                 -- 'commit'|'spend'|'refund'|'proceeds'
      amount_wei TEXT NOT NULL,
      ts         INTEGER NOT NULL,
      note       TEXT
    );

    CREATE UNIQUE INDEX IF NOT EXISTS mints_slug_idx     ON mints(slug);
    CREATE INDEX        IF NOT EXISTS mints_status_idx   ON mints(status);
    CREATE INDEX        IF NOT EXISTS mints_start_idx    ON mints(start_ts);
    CREATE INDEX        IF NOT EXISTS txs_mint_idx       ON txs(mint_id);
    CREATE UNIQUE INDEX IF NOT EXISTS txs_hash_idx       ON txs(hash) WHERE hash IS NOT NULL;
    CREATE INDEX        IF NOT EXISTS positions_mint_idx ON positions(mint_id);
    CREATE INDEX        IF NOT EXISTS positions_status_idx ON positions(status);
    CREATE INDEX        IF NOT EXISTS approvals_mint_idx ON approvals(mint_id, kind);
    CREATE INDEX        IF NOT EXISTS ledger_mint_idx    ON budget_ledger(mint_id);
    CREATE INDEX        IF NOT EXISTS ledger_kind_idx    ON budget_ledger(kind);
  `);

  // Defensive migrations for DBs created before a column existed.
  ensureColumn(db, 'mints', 'gas_plan', 'TEXT');
}

/** Add a column if the table doesn't already have it (SQLite lacks IF NOT EXISTS for ADD COLUMN). */
function ensureColumn(
  db: Database.Database,
  table: string,
  column: string,
  definition: string,
): void {
  const cols = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
  if (!cols.some((c) => c.name === column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition};`);
  }
}
