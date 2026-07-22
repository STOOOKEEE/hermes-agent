import type { PublicClient } from 'viem';
import { getChain, SEADROP_ADDRESS, type ChainId } from '../config/chains.js';
import { publicClientFor } from '../onchain/clients.js';
import { SEADROP_ABI, type PublicDrop } from '../onchain/abi.js';
import { getCollection, getDrop, pickContract } from '../sources/opensea.js';
import { logger } from '../utils/logger.js';
import type { ResolvedMintData } from '../db/index.js';

const log = logger.child({ src: 'resolve' });

/**
 * Read the SeaDrop public-drop config for an NFT contract. Null if none.
 * Accepts an explicit client (testability); falls back to the chain's client.
 */
export async function readPublicDrop(
  chain: ChainId,
  nftContract: `0x${string}`,
  client: PublicClient = publicClientFor(chain),
): Promise<PublicDrop | null> {
  try {
    const d = await client.readContract({
      address: SEADROP_ADDRESS,
      abi: SEADROP_ABI,
      functionName: 'getPublicDrop',
      args: [nftContract],
    });
    return {
      mintPrice: d.mintPrice,
      startTime: Number(d.startTime),
      endTime: Number(d.endTime),
      maxTotalMintableByWallet: Number(d.maxTotalMintableByWallet),
      feeBps: Number(d.feeBps),
      restrictFeeRecipients: d.restrictFeeRecipients,
    };
  } catch (err) {
    log.debug({ err: (err as Error).message, nftContract }, 'getPublicDrop reverted/absent');
    return null;
  }
}

/**
 * Resolve an OpenSea collection slug to a mint config. The contract address is
 * fetched from OpenSea automatically — you never paste it by hand.
 * Phase 1: SeaDrop public only; otherwise mint_kind='manual' (human in the loop).
 */
export async function resolve(slug: string, chain: ChainId): Promise<ResolvedMintData> {
  const collection = await getCollection(slug);
  if (!collection) {
    throw new Error(`OpenSea collection not found for slug "${slug}"`);
  }
  const contract = pickContract(collection, chain);
  if (!contract) {
    throw new Error(`No contract found for slug "${slug}" on ${chain}`);
  }
  return resolveByContract(contract as `0x${string}`, chain, collection.name);
}

/** Resolve a named OpenSea stage (for example "GTD" or "FCFS"). */
export async function resolveDropStage(
  slug: string,
  chain: ChainId,
  stageLabel: string,
  opts: { fallbackPublic?: boolean } = {},
): Promise<ResolvedMintData> {
  const drop = await getDrop(slug);
  if (!drop) throw new Error(`OpenSea drop not found for slug "${slug}"`);
  if (drop.chain !== chain) {
    throw new Error(`drop "${slug}" is on ${drop.chain}, not ${chain}`);
  }
  const wanted = stageLabel.trim().toLowerCase();
  const stage = drop.stages.find((s) => s.label.trim().toLowerCase() === wanted);
  if (!stage) {
    throw new Error(
      `stage "${stageLabel}" not found; available: ${drop.stages.map((s) => s.label.trim()).join(', ')}`,
    );
  }
  const start = Date.parse(stage.start_time);
  const publicStage = opts.fallbackPublic
    ? drop.stages.find((s) => s.stage_type === 'public_sale')
    : undefined;
  const end = Date.parse(publicStage?.end_time ?? stage.end_time);
  if (!Number.isFinite(start) || !Number.isFinite(end) || !/^\d+$/.test(stage.price)) {
    throw new Error(`invalid OpenSea stage data for "${stage.label.trim()}"`);
  }
  return {
    contract: drop.contract_address,
    // Keep using OpenSea's transaction builder across the fallback window. It
    // automatically targets whichever signed/public stage is active.
    mint_kind:
      stage.stage_type === 'public_sale' && !opts.fallbackPublic
        ? 'seadrop_public'
        : 'seadrop_allowlist',
    price_wei: BigInt(stage.price),
    start_ts: Math.floor(start / 1000),
    end_ts: Math.floor(end / 1000),
    max_per_wallet: Number(stage.max_per_wallet) || null,
    fee_recipient: stage.stage_type === 'public_sale' ? OPENSEA_FEE_RECIPIENT : null,
    name: drop.collection_name,
  };
}

/**
 * Resolve directly from a known NFT contract address (no OpenSea lookup).
 * Use when you already have the address, or when the slug lookup fails.
 */
export async function resolveByContract(
  nftContract: `0x${string}`,
  chain: ChainId,
  name?: string,
): Promise<ResolvedMintData> {
  const drop = await readPublicDrop(chain, nftContract);

  if (drop && drop.mintPrice >= 0n && drop.endTime > 0) {
    return {
      contract: nftContract,
      mint_kind: 'seadrop_public',
      price_wei: drop.mintPrice,
      start_ts: drop.startTime || null,
      end_ts: drop.endTime || null,
      max_per_wallet: drop.maxTotalMintableByWallet || null,
      // When restrictFeeRecipients is false, the minter must still pass an
      // allowed recipient; the canonical OpenSea recipient is always allowed.
      fee_recipient: OPENSEA_FEE_RECIPIENT,
      name: name ?? null,
    };
  }

  log.warn({ chain, nftContract }, 'no reliable SeaDrop public drop — marking manual');
  return {
    contract: nftContract,
    mint_kind: 'manual',
    price_wei: 0n,
    start_ts: null,
    end_ts: null,
    max_per_wallet: null,
    fee_recipient: null,
    name: name ?? null,
  };
}

/**
 * Canonical OpenSea fee recipient (same on Ethereum & Base). When a drop has
 * restrictFeeRecipients=false, the minter must still pass an allowed recipient;
 * the OpenSea recipient is always allowed.
 */
export const OPENSEA_FEE_RECIPIENT =
  '0x0000a26b00c1F0DF003000390027140000fAa719' as const;

// Re-export for callers that want to map a string chain to ChainId.
export function asChainId(chain: string): ChainId {
  const c = getChain(chain as ChainId);
  return c.id;
}
