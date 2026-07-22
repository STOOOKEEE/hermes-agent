/**
 * artbytes-engine CLI — local control over the engine DB.
 *
 * Usage:
 *   node dist/control/cli.js mint:add (--slug <slug> | --contract 0x..) --chain ethereum
 *        [--name N] [--decision mint-1] [--qty 1] [--budget 0.05] [--wallet W_raid]
 *        [--priority-gwei 3] [--basefee-mult 3] [--max-gas-eth 0.02]   # per-mint gas override
 *   node dist/control/cli.js approve <id>
 *   node dist/control/cli.js cancel <id>
 *   node dist/control/cli.js status
 */
import {
  addMint,
  updateMintResolved,
  setMintBudget,
  getMintBySlug,
  listAllMints,
  type ChainName,
  type Decision,
} from '../db/index.js';
import { resolve, resolveByContract, resolveDropStage } from '../resolve/index.js';
import { getCollection, pickContract } from '../sources/opensea.js';
import { approveMint, cancelMint } from '../approval/index.js';
import * as budget from '../budget/index.js';
import { ethToWei, weiToEth } from '../utils/format.js';

function parseFlags(argv: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith('--')) {
      out[key] = next;
      i++;
    } else {
      out[key] = 'true';
    }
  }
  return out;
}

async function cmdMintAdd(flags: Record<string, string>): Promise<void> {
  const slug = flags.slug;
  const contract = flags.contract;
  const chain: ChainName = flags.chain === 'base' ? 'base' : 'ethereum';
  if (!slug && !contract) throw new Error('--slug or --contract is required');

  if (slug && getMintBySlug(slug)) {
    throw new Error(`mint with slug "${slug}" already exists`);
  }

  // Optional per-mint gas override.
  const gasPlan: Record<string, number> = {};
  if (flags['priority-gwei']) gasPlan.priorityGwei = Number(flags['priority-gwei']);
  if (flags['basefee-mult']) gasPlan.baseFeeMult = Number(flags['basefee-mult']);
  if (flags['max-gas-eth']) gasPlan.maxGasEth = Number(flags['max-gas-eth']);

  // Resolve FIRST (so a failure leaves no orphan row in the DB).
  console.log(`Resolving ${slug ?? contract} on ${chain}…`);
  let resolved;
  if (flags.stage) {
    if (!slug) throw new Error('--stage requires --slug');
    resolved = await resolveDropStage(slug, chain, flags.stage, {
      fallbackPublic: flags['fallback-public'] === 'true',
    });
    if (contract && resolved.contract.toLowerCase() !== contract.toLowerCase()) {
      throw new Error(
        `mismatch: stage resolves to ${resolved.contract} but --contract is ${contract} — refusing`,
      );
    }
  } else if (contract) {
    let name = flags.name;
    // If a slug is also given, cross-check it points to the SAME contract.
    // A mismatch (typo / wrong address) is refused — capital-first.
    if (slug) {
      const col = await getCollection(slug);
      if (col) {
        const osContract = pickContract(col, chain);
        if (osContract && osContract.toLowerCase() !== contract.toLowerCase()) {
          throw new Error(
            `mismatch: slug "${slug}" resolves to ${osContract} but --contract is ${contract} — refusing`,
          );
        }
        name = name ?? col.name;
      }
    }
    resolved = await resolveByContract(contract as `0x${string}`, chain, name);
  } else {
    resolved = await resolve(slug!, chain);
  }

  const mint = addMint({
    slug,
    name: flags.name ?? resolved.name ?? undefined,
    chain,
    decision: (flags.decision as Decision) ?? 'watch',
    qty_target: flags.qty ? Number(flags.qty) : 1,
    budget_wei: flags.budget ? ethToWei(flags.budget) : undefined,
    gas_plan: Object.keys(gasPlan).length > 0 ? gasPlan : undefined,
    wl_wallet: flags.wallet,
  });
  updateMintResolved(mint.id, resolved);
  if (flags.budget) setMintBudget(mint.id, ethToWei(flags.budget));

  console.log(
    JSON.stringify(
      {
        id: mint.id,
        slug: slug ?? null,
        chain,
        contract: resolved.contract,
        mint_kind: resolved.mint_kind,
        price_eth: weiToEth(resolved.price_wei),
        start_ts: resolved.start_ts,
        max_per_wallet: resolved.max_per_wallet,
        gas_plan: Object.keys(gasPlan).length > 0 ? gasPlan : 'env defaults',
      },
      null,
      2,
    ),
  );
}

function cmdStatus(): void {
  const s = budget.snapshot();
  const mints = listAllMints();
  console.log(
    JSON.stringify(
      {
        ledger: {
          committed_eth: weiToEth(s.committedWei),
          spent_eth: weiToEth(s.spentWei),
          proceeds_eth: weiToEth(s.proceedsWei),
        },
        mints: mints.map((m) => ({
          id: m.id,
          slug: m.slug,
          status: m.status,
          mint_kind: m.mint_kind,
          decision: m.decision,
          price_eth: m.price_wei ? weiToEth(BigInt(m.price_wei)) : null,
          budget_eth: m.budget_wei ? weiToEth(BigInt(m.budget_wei)) : null,
        })),
      },
      null,
      2,
    ),
  );
}

async function main(): Promise<void> {
  const [cmd, ...rest] = process.argv.slice(2);
  const flags = parseFlags(rest);

  switch (cmd) {
    case 'mint:add':
      await cmdMintAdd(flags);
      break;
    case 'approve': {
      const id = Number(rest[0]);
      if (!Number.isInteger(id)) throw new Error('usage: approve <id>');
      console.log(approveMint(id) ? `approved #${id}` : `failed #${id}`);
      break;
    }
    case 'cancel': {
      const id = Number(rest[0]);
      if (!Number.isInteger(id)) throw new Error('usage: cancel <id>');
      console.log(cancelMint(id) ? `cancelled #${id}` : `cannot cancel #${id}`);
      break;
    }
    case 'status':
      cmdStatus();
      break;
    default:
      console.log('commands: mint:add | approve <id> | cancel <id> | status');
  }
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(`error: ${(err as Error).message}`);
    process.exit(1);
  });
