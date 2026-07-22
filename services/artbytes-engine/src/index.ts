import { Bot } from 'grammy';
import { env } from './config/env.js';
import { logger } from './utils/logger.js';
import { LogNotifier, TelegramNotifier, type Notifier } from './notify/index.js';
import { ApprovalGate } from './approval/index.js';
import { MonitorLoop } from './monitor/index.js';
import { registerCommands } from './bot/commands.js';
import { listConfiguredWallets } from './wallets/index.js';
import * as budget from './budget/index.js';
import { weiToEth } from './utils/format.js';

async function main(): Promise<void> {
  const log = logger.child({ src: 'main' });
  log.info('🚀 artbytes-engine starting');

  const telegramEnabled = Boolean(env.telegram.botToken && env.telegram.userId);
  const bot = telegramEnabled ? new Bot(env.telegram.botToken) : null;
  const notifier: Notifier = bot ? new TelegramNotifier(bot) : new LogNotifier();
  const gate = new ApprovalGate(notifier);
  const monitor = new MonitorLoop(notifier, gate);

  if (bot) {
    registerCommands(bot, gate);
    const me = await bot.api.getMe();
    log.info({ bot: me.username }, '🤖 bot connected');
  }

  if (bot) try {
    const s = budget.snapshot();
    await bot.api.sendMessage(
      env.telegram.userId,
      `🟢 <b>artbytes-engine online</b>\n` +
        `Spent so far: ${weiToEth(s.spentWei)} ETH · committed ${weiToEth(s.committedWei)} ETH\n` +
        `Per-mint budgets set by the AI. Wallets: ${listConfiguredWallets().join(', ') || 'none configured'}\n` +
        `Send /help for commands.`,
      { parse_mode: 'HTML' },
    );
  } catch (err) {
    log.warn({ err }, 'could not send boot message (check TELEGRAM_USER_ID)');
  }

  monitor.start();

  if (bot) {
    void bot.start({
      onStart: (info) => log.info({ username: info.username }, '🤖 bot polling started'),
    });
  } else {
    log.info('Telegram disabled; using headless log notifier');
  }

  const shutdown = async (signal: string) => {
    log.info({ signal }, '🛑 shutting down');
    monitor.stop();
    try {
      await bot?.stop();
    } catch {
      /* ignore */
    }
    process.exit(0);
  };
  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('unhandledRejection', (err) => log.error({ err }, 'unhandledRejection'));
  process.on('uncaughtException', (err) => log.error({ err }, 'uncaughtException'));
}

main().catch((err) => {
  logger.fatal({ err }, 'fatal error');
  process.exit(1);
});
