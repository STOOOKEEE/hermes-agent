import 'dotenv/config';
import { parseEther } from 'viem';
import { readFileSync } from 'node:fs';

function required(name: string): string {
  const v = process.env[name];
  if (!v || v.trim() === '') {
    throw new Error(`Missing required env var: ${name}`);
  }
  return v.trim();
}

/** Read a secret from NAME_FILE first, then fall back to NAME. */
function secretRequired(name: string): string {
  const file = process.env[`${name}_FILE`]?.trim();
  if (file) {
    const value = readFileSync(file, 'utf8').trim();
    if (!value) throw new Error(`Secret file for ${name} is empty: ${file}`);
    return value;
  }
  return required(name);
}

function optional(name: string, fallback: string): string {
  return process.env[name]?.trim() || fallback;
}

function num(name: string, fallback: number): number {
  const v = process.env[name];
  if (!v) return fallback;
  const n = Number(v);
  if (Number.isNaN(n)) throw new Error(`Env ${name} is not a number: ${v}`);
  return n;
}

function bool(name: string, fallback: boolean): boolean {
  const v = process.env[name]?.trim().toLowerCase();
  if (v === undefined || v === '') return fallback;
  return v === 'true' || v === '1' || v === 'yes';
}

export const env = {
  eth: {
    rpcUrl: required('ETH_RPC_URL'),
  },
  base: {
    rpcUrl: optional('BASE_RPC_URL', ''),
  },
  opensea: {
    apiKey: secretRequired('OPENSEA_API_KEY'),
  },
  etherscan: {
    apiKey: optional('ETHERSCAN_API_KEY', ''),
  },
  basescan: {
    apiKey: optional('BASESCAN_API_KEY', ''),
  },
  wallets: {
    raidPkFile: optional('WALLET_RAID_PK_FILE', ''),
    kryskoPkFile: optional('WALLET_KRYSKO_PK_FILE', ''),
  },
  gas: {
    // Absolute gas ceiling per TX (technical anti-gas-war guard, not a spend cap).
    maxGasWei: parseEther(optional('MAX_GAS_ETH', '0.01')),
    maxPriorityGwei: num('MAX_PRIORITY_GWEI', 1.5),
    baseFeeMult: num('BASEFEE_MULT', 2),
  },
  approval: {
    // Global kill-switch; never auto-approve unless explicitly opted-in per mint.
    autoApprove: bool('AUTO_APPROVE', false),
    timeoutMs: num('APPROVAL_TIMEOUT_MS', 2 * 60 * 60 * 1000),
  },
  telegram: {
    botToken: optional('TELEGRAM_BOT_TOKEN', ''),
    userId: num('TELEGRAM_USER_ID', 0),
  },
  gateway: {
    url: optional('GATEWAY_URL', ''),
  },
  control: {
    port: num('CONTROL_PORT', 8743),
    token: optional('CONTROL_TOKEN', ''),
  },
  app: {
    dbPath: optional('DB_PATH', './data/engine.db'),
    logLevel: optional('LOG_LEVEL', 'info'),
    monitorTickMs: num('MONITOR_TICK_MS', 60_000),
  },
} as const;

export type AppEnv = typeof env;
