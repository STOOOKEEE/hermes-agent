import { Bot, Context } from 'grammy';
import { env } from '../config/env.js';
import { listAllMints, getMint } from '../db/index.js';
import * as budget from '../budget/index.js';
import { logger } from '../utils/logger.js';
import { escapeHtml, weiToEth, shortAddress } from '../utils/format.js';
import type { ApprovalGate } from '../approval/index.js';

const log = logger.child({ src: 'bot' });

function authMiddleware(ctx: Context, next: () => Promise<void>): Promise<void> {
  const uid = ctx.from?.id;
  if (uid !== env.telegram.userId) {
    log.warn({ uid }, 'unauthorized bot interaction');
    return Promise.resolve();
  }
  return next();
}

function budgetLine(): string {
  const s = budget.snapshot();
  return (
    `💰 <b>Ledger</b> (per-mint budgets set by the AI)\n` +
    `  committed <b>${weiToEth(s.committedWei)}</b> · spent ${weiToEth(s.spentWei)}\n` +
    `  proceeds ${weiToEth(s.proceedsWei)} (info)`
  );
}

export function registerCommands(bot: Bot, gate: ApprovalGate): void {
  bot.use(authMiddleware);

  bot.command('start', async (ctx) => {
    await ctx.reply('👋 artbytes-engine online. /help for commands.');
  });

  bot.command('help', async (ctx) => {
    await ctx.reply(
      [
        '<b>artbytes-engine — Commands</b>',
        '',
        '<code>/status</code> — budget + mint counts',
        '<code>/mints</code> — list tracked mints',
        '<code>/approve &lt;id&gt;</code> — approve a mint',
        '<code>/cancel &lt;id&gt;</code> — cancel a mint (releases budget)',
        '<code>/ping</code> — health check',
      ].join('\n'),
      { parse_mode: 'HTML' },
    );
  });

  bot.command('ping', async (ctx) => {
    await ctx.reply(`🟢 pong · ${new Date().toISOString()}`);
  });

  bot.command('status', async (ctx) => {
    const mints = listAllMints();
    const byStatus = mints.reduce<Record<string, number>>((acc, m) => {
      acc[m.status] = (acc[m.status] ?? 0) + 1;
      return acc;
    }, {});
    const counts = Object.entries(byStatus)
      .map(([k, v]) => `${k}: ${v}`)
      .join(' · ');
    await ctx.reply(`${budgetLine()}\n\n📋 <b>Mints</b> — ${counts || 'none'}`, {
      parse_mode: 'HTML',
    });
  });

  bot.command('mints', async (ctx) => {
    const mints = listAllMints().slice(0, 25);
    if (mints.length === 0) {
      await ctx.reply('No mints tracked yet.');
      return;
    }
    const lines = mints.map((m) => {
      const name = escapeHtml(m.name || m.slug || `#${m.id}`);
      const price = m.price_wei ? ` · ${weiToEth(BigInt(m.price_wei))}Ξ` : '';
      const c = m.contract ? ` · ${shortAddress(m.contract)}` : '';
      return `• <code>${m.id}</code> ${name} — <i>${m.status}</i>${price}${c}`;
    });
    await ctx.reply(`<b>Tracked mints:</b>\n\n${lines.join('\n')}`, {
      parse_mode: 'HTML',
    });
  });

  bot.command('approve', async (ctx) => {
    const id = Number((ctx.match ?? '').toString().trim());
    if (!Number.isInteger(id)) {
      await ctx.reply('Usage: <code>/approve &lt;id&gt;</code>', { parse_mode: 'HTML' });
      return;
    }
    if (!getMint(id)) {
      await ctx.reply(`No mint #${id}`);
      return;
    }
    gate.approve(id);
    await ctx.reply(`✅ Approved mint #${id}. Will mint when live + under cap.`);
  });

  bot.command('cancel', async (ctx) => {
    const id = Number((ctx.match ?? '').toString().trim());
    if (!Number.isInteger(id)) {
      await ctx.reply('Usage: <code>/cancel &lt;id&gt;</code>', { parse_mode: 'HTML' });
      return;
    }
    const ok = gate.cancel(id);
    await ctx.reply(ok ? `❌ Cancelled mint #${id} (budget released).` : `Cannot cancel #${id} (already minting/minted).`);
  });

  bot.callbackQuery(/^approve:(\d+)$/, async (ctx) => {
    const id = Number(ctx.match[1]);
    gate.approve(id);
    await ctx.answerCallbackQuery({ text: `Approved #${id}` });
    await ctx.reply(`✅ Approved mint #${id}.`);
  });

  bot.callbackQuery(/^cancel:(\d+)$/, async (ctx) => {
    const id = Number(ctx.match[1]);
    const ok = gate.cancel(id);
    await ctx.answerCallbackQuery({ text: ok ? `Cancelled #${id}` : `Cannot cancel #${id}` });
    await ctx.reply(ok ? `❌ Cancelled mint #${id}.` : `Cannot cancel #${id} (already minting/minted).`);
  });

  bot.catch((err) => {
    log.error({ err }, 'bot error');
  });
}
