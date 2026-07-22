// Must be imported FIRST in every test file, before any src/ import:
// it sets env vars that src/config/env.ts reads at module-evaluation time.
// A per-process DB file keeps parallel test files isolated.
process.env.DB_PATH = `./data/.test-db-${process.pid}.db`;
process.env.TELEGRAM_BOT_TOKEN = 'test:token';
process.env.TELEGRAM_USER_ID = '1';
process.env.ETH_RPC_URL = 'http://localhost:8545';
process.env.OPENSEA_API_KEY = 'test-key';
process.env.MAX_GAS_ETH = '0.01';
process.env.LOG_LEVEL = 'silent';

import { unlinkSync } from 'node:fs';

export function cleanupDb(): void {
  const p = process.env.DB_PATH!;
  for (const suffix of ['', '-shm', '-wal']) {
    try {
      unlinkSync(p + suffix);
    } catch {
      /* ignore */
    }
  }
}
