import { Bot, InlineKeyboard } from 'grammy';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';
import { escapeHtml, weiToEth, shortAddress } from '../utils/format.js';
import { getChain, explorerTxUrl } from '../config/chains.js';
import type { Mint } from '../db/index.js';

const log = logger.child({ src: 'notify' });
const TEXT_LIMIT = 4096;

export interface MintResult {
  mint: Mint;
  wallet: string;
  tokenIds: string[];
  totalCostWei: bigint;
  gasWei: bigint;
  txHash: string;
}

export interface Notifier {
  mintT24h(mint: Mint): Promise<void>;
  mintLive(mint: Mint): Promise<void>;
  approvalRequest(mint: Mint, opts: { qty: number; costWei: bigint }): Promise<void>;
  mintDone(result: MintResult): Promise<void>;
  mintFailed(mint: Mint, reason: string): Promise<void>;
  manualMint(mint: Mint): Promise<void>;
  error(ctx: string, message: string): Promise<void>;
}

/** Headless notifier used when Telegram is intentionally disabled. */
export class LogNotifier implements Notifier {
  async mintT24h(mint: Mint): Promise<void> { log.info({ mintId: mint.id }, 'T-24h'); }
  async mintLive(mint: Mint): Promise<void> { log.info({ mintId: mint.id }, 'mint live'); }
  async approvalRequest(mint: Mint, opts: { qty: number; costWei: bigint }): Promise<void> {
    log.warn({ mintId: mint.id, qty: opts.qty, maxCostWei: opts.costWei.toString() }, 'approval needed');
  }
  async mintDone(result: MintResult): Promise<void> {
    log.info({ mintId: result.mint.id, txHash: result.txHash, totalCostWei: result.totalCostWei.toString() }, 'mint done');
  }
  async mintFailed(mint: Mint, reason: string): Promise<void> { log.error({ mintId: mint.id, reason }, 'mint aborted'); }
  async manualMint(mint: Mint): Promise<void> { log.warn({ mintId: mint.id }, 'manual mint required'); }
  async error(ctx: string, message: string): Promise<void> { log.error({ ctx, message }, 'engine error'); }
}

/** Human-readable Telegram notifications. No raw JSON. */
export class TelegramNotifier implements Notifier {
  constructor(private bot: Bot) {}

  private async send(text: string, keyboard?: InlineKeyboard): Promise<void> {
    try {
      await this.bot.api.sendMessage(env.telegram.userId, text.slice(0, TEXT_LIMIT), {
        parse_mode: 'HTML',
        reply_markup: keyboard,
        link_preview_options: { is_disabled: true },
      });
    } catch (err) {
      log.error({ err }, 'failed to send Telegram message');
    }
  }

  private mintLabel(mint: Mint): string {
    return escapeHtml(mint.name || mint.slug || `mint #${mint.id}`);
  }

  async mintT24h(mint: Mint): Promise<void> {
    const when = mint.start_ts ? new Date(mint.start_ts * 1000).toISOString() : 'soon';
    await this.send(
      [
        `⏰ <b>T-24h</b> · ${this.mintLabel(mint)}`,
        `Chain: <i>${mint.chain}</i> · Decision: <code>${mint.decision ?? '-'}</code>`,
        mint.price_wei ? `Price: ${weiToEth(BigInt(mint.price_wei))} ETH/unit` : '',
        mint.budget_wei ? `Budget: ${weiToEth(BigInt(mint.budget_wei))} ETH` : '',
        `Starts: <code>${when}</code>`,
        '',
        `Confirm the plan & budget before T-0.`,
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }

  async mintLive(mint: Mint): Promise<void> {
    await this.send(
      `🟢 <b>LIVE</b> · ${this.mintLabel(mint)} mint is open. Evaluating execution…`,
    );
  }

  async approvalRequest(mint: Mint, opts: { qty: number; costWei: bigint }): Promise<void> {
    const kb = new InlineKeyboard()
      .text('✅ Approve', `approve:${mint.id}`)
      .text('❌ Cancel', `cancel:${mint.id}`);
    await this.send(
      [
        `🔐 <b>Approval needed</b> · ${this.mintLabel(mint)}`,
        `Chain: <i>${mint.chain}</i>`,
        mint.contract ? `Contract: <code>${shortAddress(mint.contract)}</code>` : '',
        `Qty: <b>${opts.qty}</b>`,
        mint.price_wei ? `Unit price: ${weiToEth(BigInt(mint.price_wei))} ETH` : '',
        `Max cost (mint+gas): <b>${weiToEth(opts.costWei)} ETH</b>`,
        '',
        `Reply <code>/approve ${mint.id}</code> or <code>/cancel ${mint.id}</code>.`,
      ]
        .filter(Boolean)
        .join('\n'),
      kb,
    );
  }

  async mintDone(result: MintResult): Promise<void> {
    const chain = getChain(result.mint.chain);
    const txUrl = explorerTxUrl(chain, result.txHash);
    await this.send(
      [
        `🎉 <b>Minted</b> · ${this.mintLabel(result.mint)}`,
        `Tokens: <b>${result.tokenIds.length}</b>` +
          (result.tokenIds.length ? ` (${result.tokenIds.slice(0, 8).join(', ')})` : ''),
        `Wallet: <code>${shortAddress(result.wallet)}</code>`,
        `Total cost: <b>${weiToEth(result.totalCostWei)} ETH</b> (gas ${weiToEth(result.gasWei)})`,
        `Tx: <a href="${txUrl}">${result.txHash.slice(0, 10)}…</a>`,
      ].join('\n'),
    );
  }

  async mintFailed(mint: Mint, reason: string): Promise<void> {
    await this.send(
      `⚠️ <b>Mint aborted</b> · ${this.mintLabel(mint)}\nReason: ${escapeHtml(reason)}`,
    );
  }

  async manualMint(mint: Mint): Promise<void> {
    await this.send(
      [
        `✋ <b>Manual mint NOW</b> · ${this.mintLabel(mint)}`,
        mint.contract ? `Contract: <code>${mint.contract}</code>` : '',
        `The engine cannot auto-mint this one (mint_kind=manual). Mint via the front-end.`,
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }

  async error(ctx: string, message: string): Promise<void> {
    await this.send(`🔴 <b>${escapeHtml(ctx)}</b>\n${escapeHtml(message)}`);
  }
}
