import { readFileSync, statSync } from 'node:fs';
import { privateKeyToAccount } from 'viem/accounts';
import type { Account, Hex } from 'viem';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';

const log = logger.child({ src: 'wallets' });

export type WalletName = 'W_raid' | 'W_krysko';

export interface Signer {
  name: WalletName;
  account: Account;
  address: string;
}

const PK_FILES: Record<WalletName, string> = {
  W_raid: env.wallets.raidPkFile,
  W_krysko: env.wallets.kryskoPkFile,
};

const cache = new Map<WalletName, Signer>();

/**
 * Load a wallet from its private-key file (chmod 600, on the VM only).
 * The key is never logged, never stored in the DB, never returned.
 */
export function loadWallet(name: WalletName): Signer {
  const cached = cache.get(name);
  if (cached) return cached;

  const file = PK_FILES[name];
  if (!file) throw new Error(`No PK file configured for wallet ${name}`);

  // Warn loudly if the key file is more permissive than 0600.
  try {
    const mode = statSync(file).mode & 0o777;
    if (mode & 0o077) {
      log.warn({ name, file, mode: mode.toString(8) }, 'PK file is not chmod 600');
    }
  } catch (err) {
    throw new Error(`Cannot stat PK file for ${name}: ${(err as Error).message}`);
  }

  const raw = readFileSync(file, 'utf8').trim();
  const pk = (raw.startsWith('0x') ? raw : `0x${raw}`) as Hex;
  if (!/^0x[0-9a-fA-F]{64}$/.test(pk)) {
    throw new Error(`PK file for ${name} is not a valid 32-byte hex private key`);
  }

  const account = privateKeyToAccount(pk);
  const signer: Signer = { name, account, address: account.address };
  cache.set(name, signer);
  log.info({ name, address: signer.address }, 'wallet loaded');
  return signer;
}

export function listConfiguredWallets(): WalletName[] {
  return (Object.keys(PK_FILES) as WalletName[]).filter((n) => PK_FILES[n]);
}
