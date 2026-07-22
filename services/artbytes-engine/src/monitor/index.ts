import { env } from '../config/env.js';
import { resolveGas, parseGasPlan } from '../config/gas.js';
import {
  listMintsByStatus,
  markT24Notified,
  updateMintStatus,
  type Mint,
  type Decision,
} from '../db/index.js';
import { publicClientFor, walletClientFor } from '../onchain/clients.js';
import { loadWallet, type WalletName } from '../wallets/index.js';
import { executeMint } from '../mint/executor.js';
import { logger } from '../utils/logger.js';
import type { Notifier } from '../notify/index.js';
import type { ApprovalGate } from '../approval/index.js';

const log = logger.child({ src: 'monitor' });

const DAY = 24 * 60 * 60;
const T24_WINDOW = 60 * 60; // notify within this slack of the 24h mark
const ACTIONABLE: Decision[] = ['mint-1', 'mint-conviction', 'max'];

function unixNow(): number {
  return Math.floor(Date.now() / 1000);
}

function isLive(mint: Mint, now: number): boolean {
  if (!mint.start_ts) return false;
  if (mint.start_ts > now) return false;
  if (mint.end_ts && now > mint.end_ts) return false;
  return true;
}

function effectiveQty(mint: Mint): number {
  const target = mint.qty_target ?? 1;
  const cap = mint.max_per_wallet ?? target;
  return Math.max(1, Math.min(target, cap || target));
}

function walletFor(mint: Mint): WalletName {
  return (mint.wl_wallet as WalletName) || 'W_raid';
}

export class MonitorLoop {
  private timer: NodeJS.Timeout | null = null;
  private ticking = false;

  constructor(
    private notifier: Notifier,
    private gate: ApprovalGate,
  ) {}

  start(): void {
    log.info({ tickMs: env.app.monitorTickMs }, 'monitor started');
    this.timer = setInterval(() => void this.safeTick(), env.app.monitorTickMs);
    void this.safeTick();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private async safeTick(): Promise<void> {
    if (this.ticking) return;
    this.ticking = true;
    try {
      await this.tick();
    } catch (err) {
      log.error({ err }, 'monitor tick failed');
    } finally {
      this.ticking = false;
    }
  }

  async tick(): Promise<void> {
    this.gate.expireStale();
    const now = unixNow();
    const mints = listMintsByStatus('watching', 'approved');

    for (const mint of mints) {
      // T-24h notice
      if (
        mint.start_ts &&
        !mint.notified_t24 &&
        mint.start_ts - now <= DAY + T24_WINDOW &&
        mint.start_ts - now > DAY - T24_WINDOW
      ) {
        await this.notifier.mintT24h(mint);
        markT24Notified(mint.id);
      }

      if (!ACTIONABLE.includes(mint.decision ?? 'watch')) continue;
      if (!isLive(mint, now)) continue;

      if (mint.mint_kind === 'manual') {
        if (mint.status === 'watching') {
          await this.notifier.manualMint(mint);
          updateMintStatus(mint.id, 'passed');
        }
        continue;
      }

      if (mint.status === 'watching') {
        // Live but not yet approved → request human approval.
        const qty = effectiveQty(mint);
        const gasCfg = resolveGas(parseGasPlan(mint.gas_plan));
        const cost = BigInt(mint.price_wei ?? '0') * BigInt(qty) + gasCfg.maxGasWei;
        await this.gate.request(mint, { qty, costWei: cost });
        continue;
      }

      // status === 'approved' && live → execute.
      await this.executeOne(mint);
    }
  }

  private async executeOne(mint: Mint): Promise<void> {
    try {
      const signer = loadWallet(walletFor(mint));
      const publicClient = publicClientFor(mint.chain);
      const walletClient = walletClientFor(mint.chain, signer.account);
      await executeMint(mint.id, {
        publicClient,
        walletClient,
        account: signer.account,
        notifier: this.notifier,
        gate: this.gate,
      });
    } catch (err) {
      log.error({ mintId: mint.id, err }, 'executeOne failed');
      await this.notifier.error('mint execution', (err as Error).message);
    }
  }
}
