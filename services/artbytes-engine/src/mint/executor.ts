import {
  parseEventLogs,
  zeroAddress,
  type PublicClient,
  type WalletClient,
  type Account,
} from 'viem';
import { resolveGas, parseGasPlan } from '../config/gas.js';
import {
  getMint,
  hasMintTx,
  insertTx,
  updateTxStatus,
  updateMintStatus,
  updateMintResolved,
  insertPosition,
  type Mint,
} from '../db/index.js';
import { getChain } from '../config/chains.js';
import * as budget from '../budget/index.js';
import { BudgetExceededError, NoBudgetError } from '../budget/index.js';
import { buildMintCall } from './resolver.js';
import { readPublicDrop } from '../resolve/index.js';
import { buildGasParams } from '../onchain/clients.js';
import { ERC721_ABI } from '../onchain/abi.js';
import { logger } from '../utils/logger.js';
import { weiToEth } from '../utils/format.js';
import type { Notifier } from '../notify/index.js';
import type { ApprovalGate } from '../approval/index.js';
import type { MintResult } from '../notify/index.js';
import {
  buildDropMintTransaction,
  type OpenSeaMintAction,
} from '../sources/opensea.js';

const log = logger.child({ src: 'executor' });

export interface ExecuteDeps {
  publicClient: PublicClient;
  walletClient: WalletClient;
  account: Account;
  notifier: Notifier;
  gate: ApprovalGate;
  buildOpenSeaMint?: typeof buildDropMintTransaction;
}

/** Clamp the desired quantity to the per-wallet drop limit. */
function effectiveQty(mint: Mint): number {
  const target = mint.qty_target ?? 1;
  const cap = mint.max_per_wallet ?? target;
  return Math.max(1, Math.min(target, cap || target));
}

/**
 * Execute a mint under the full safety sequence:
 *   idempotence → re-resolve → approval → reserve → simulate → gas → write →
 *   receipt → ledger + positions.
 * Returns the MintResult on success, or null on any abort/skip (capital-first).
 */
export async function executeMint(
  mintId: number,
  deps: ExecuteDeps,
): Promise<MintResult | null> {
  const { publicClient, walletClient, account, notifier, gate } = deps;
  let mint = getMint(mintId);
  if (!mint) {
    log.warn({ mintId }, 'executeMint: mint not found');
    return null;
  }

  // 1. Idempotence — never mint the same mint twice.
  if (mint.status === 'minting' || mint.status === 'minted' || hasMintTx(mintId)) {
    log.info({ mintId, status: mint.status }, 'executeMint: already minting/minted — skip');
    return null;
  }

  // Chain guard.
  const chain = getChain(mint.chain);
  if (walletClient.chain && walletClient.chain.id !== chain.chainId) {
    await notifier.mintFailed(mint, `wrong chain: wallet on ${walletClient.chain.id}, mint on ${chain.chainId}`);
    return null;
  }

  // 2. Re-resolve drop config just before executing (price may have changed).
  if (mint.mint_kind === 'seadrop_public' && mint.contract) {
    const fresh = await readPublicDrop(mint.chain, mint.contract as `0x${string}`, publicClient);
    if (fresh) {
      updateMintResolved(mintId, {
        contract: mint.contract,
        mint_kind: 'seadrop_public',
        price_wei: fresh.mintPrice,
        start_ts: fresh.startTime || null,
        end_ts: fresh.endTime || null,
        max_per_wallet: fresh.maxTotalMintableByWallet || null,
        fee_recipient: mint.fee_recipient,
      });
      mint = getMint(mintId)!;
    }
  }

  if (mint.mint_kind === 'manual') {
    await notifier.manualMint(mint);
    return null;
  }

  const qty = effectiveQty(mint);
  const price = BigInt(mint.price_wei ?? '0');
  let openSeaAction: OpenSeaMintAction | null = null;
  if (mint.mint_kind === 'seadrop_allowlist') {
    if (!mint.slug) {
      await notifier.mintFailed(mint, 'signed presale requires an OpenSea slug');
      return null;
    }
    try {
      openSeaAction = await (deps.buildOpenSeaMint ?? buildDropMintTransaction)(
        mint.slug,
        account.address as `0x${string}`,
        qty,
      );
    } catch (err) {
      await notifier.mintFailed(mint, `OpenSea mint action failed: ${(err as Error).message}`);
      return null;
    }
    const expected = price * BigInt(qty);
    if (openSeaAction.value !== expected) {
      await notifier.mintFailed(
        mint,
        `OpenSea value mismatch: expected ${expected}, got ${openSeaAction.value}`,
      );
      return null;
    }
  }
  // Per-mint gas override (AI-set) falls back to the global env defaults.
  const gasCfg = resolveGas(parseGasPlan(mint.gas_plan));
  const mintValueWei = openSeaAction?.value ?? price * BigInt(qty);
  const costWei = mintValueWei + gasCfg.maxGasWei;

  // The AI (Hermes) sets the budget per mint. No budget → never spend.
  if (mint.budget_wei == null) {
    await notifier.mintFailed(mint, 'no budget_wei set on this mint (set by the AI)');
    return null;
  }
  const mintBudgetWei = BigInt(mint.budget_wei);

  // 3. Approval gate — never spend without approval (defense in depth; the
  // monitor only calls us when already approved).
  if (!gate.isApproved(mintId)) {
    log.info({ mintId }, 'executeMint: not approved — requesting');
    await gate.request(mint, { qty, costWei });
    return null;
  }

  // 4. Reserve budget atomically against THIS mint's AI-assigned budget.
  const wallet = mint.wl_wallet ?? account.address;
  try {
    budget.reserve(mintId, wallet, costWei, mintBudgetWei, `mint ${qty}x`);
  } catch (err) {
    if (err instanceof BudgetExceededError || err instanceof NoBudgetError) {
      await notifier.mintFailed(mint, err.message);
      return null;
    }
    throw err;
  }

  const call = openSeaAction
    ? null
    : buildMintCall(mint, account.address as `0x${string}`, qty);

  try {
    // 5. Simulate — never write without a passing simulate.
    if (openSeaAction) {
      await publicClient.call({
        account,
        to: openSeaAction.target,
        data: openSeaAction.calldata,
        value: openSeaAction.value,
      });
    } else {
      await publicClient.simulateContract({
        account,
        address: call!.address,
        abi: call!.abi,
        functionName: call!.functionName,
        args: call!.args,
        value: call!.value,
      });
    }

    // 6. Gas EIP-1559 with an absolute ceiling.
    const gasUnits = openSeaAction
      ? await publicClient.estimateGas({
          account,
          to: openSeaAction.target,
          data: openSeaAction.calldata,
          value: openSeaAction.value,
        })
      : await publicClient.estimateContractGas({
          account,
          address: call!.address,
          abi: call!.abi,
          functionName: call!.functionName,
          args: call!.args,
          value: call!.value,
        });
    const gasParams = await buildGasParams(publicClient, gasUnits, {
      maxPriorityGwei: gasCfg.maxPriorityGwei,
      baseFeeMult: gasCfg.baseFeeMult,
    });
    if (gasParams.maxCostWei > gasCfg.maxGasWei) {
      budget.refund(mintId, wallet, costWei, 'gas too high');
      await notifier.mintFailed(
        mint,
        `gas too high: ${weiToEth(gasParams.maxCostWei)} ETH > max ${weiToEth(gasCfg.maxGasWei)} ETH`,
      );
      return null;
    }

    // 7. Lock + send.
    updateMintStatus(mintId, 'minting');
    const hash = openSeaAction
      ? await walletClient.sendTransaction({
          account,
          chain: chain.viemChain,
          to: openSeaAction.target,
          data: openSeaAction.calldata,
          value: openSeaAction.value,
          gas: gasParams.gas,
          maxFeePerGas: gasParams.maxFeePerGas,
          maxPriorityFeePerGas: gasParams.maxPriorityFeePerGas,
        })
      : await walletClient.writeContract({
          account,
          chain: chain.viemChain,
          address: call!.address,
          abi: call!.abi,
          functionName: call!.functionName,
          args: call!.args,
          value: call!.value,
          gas: gasParams.gas,
          maxFeePerGas: gasParams.maxFeePerGas,
          maxPriorityFeePerGas: gasParams.maxPriorityFeePerGas,
        });
    insertTx({ mint_id: mintId, kind: 'mint', wallet, hash, status: 'pending', value_wei: mintValueWei });

    // 8. Wait for receipt.
    const receipt = await publicClient.waitForTransactionReceipt({ hash });
    const gasWei = receipt.gasUsed * (receipt.effectiveGasPrice ?? 0n);

    if (receipt.status !== 'success') {
      updateTxStatus(hash, 'reverted', { gas_wei: gasWei, block: Number(receipt.blockNumber) });
      // Value is returned on revert; only gas is spent.
      budget.spend(mintId, wallet, gasWei, 'reverted gas');
      budget.refund(mintId, wallet, costWei - gasWei, 'reverted refund');
      updateMintStatus(mintId, 'failed');
      await notifier.mintFailed(mint, `tx reverted onchain (${hash.slice(0, 10)}…)`);
      return null;
    }

    updateTxStatus(hash, 'confirmed', { gas_wei: gasWei, block: Number(receipt.blockNumber) });

    // 9. Ledger reconciliation: spend actual, refund the reservation remainder.
    const totalSpent = mintValueWei + gasWei;
    budget.spend(mintId, wallet, totalSpent, 'mint');
    budget.refund(mintId, wallet, costWei - totalSpent, 'mint remainder');

    // 10. Extract minted token ids and open positions.
    const tokenIds = extractMintedTokenIds(receipt.logs, account.address);
    const costPerToken = tokenIds.length > 0 ? totalSpent / BigInt(tokenIds.length) : totalSpent;
    for (const tokenId of tokenIds) {
      insertPosition({ mint_id: mintId, token_id: tokenId, wallet, cost_wei: costPerToken });
    }
    updateMintStatus(mintId, 'minted');

    const result: MintResult = {
      mint,
      wallet,
      tokenIds,
      totalCostWei: totalSpent,
      gasWei,
      txHash: hash,
    };
    await notifier.mintDone(result);
    log.info({ mintId, tokens: tokenIds.length, cost: weiToEth(totalSpent) }, 'mint done');
    return result;
  } catch (err) {
    // Simulate revert or any pre-send failure → release the reservation.
    budget.refund(mintId, wallet, budget.committedForMint(mintId), 'abort');
    if (mint.status !== 'minting') updateMintStatus(mintId, 'failed');
    const reason = (err as Error).message.split('\n')[0];
    log.error({ mintId, err: reason }, 'executeMint aborted');
    await notifier.mintFailed(mint, reason);
    return null;
  }
}

/** Pull token ids from ERC721 Transfer logs where from=0x0 and to=minter. */
function extractMintedTokenIds(logs: readonly unknown[], minter: string): string[] {
  const parsed = parseEventLogs({
    abi: ERC721_ABI,
    eventName: 'Transfer',
    logs: logs as never,
  });
  const out: string[] = [];
  for (const ev of parsed) {
    const { from, to, tokenId } = ev.args as { from: string; to: string; tokenId: bigint };
    if (from === zeroAddress && to.toLowerCase() === minter.toLowerCase()) {
      out.push(tokenId.toString());
    }
  }
  return out;
}
