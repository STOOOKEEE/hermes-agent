import axios from 'axios';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';
import type { ChainName } from '../db/index.js';

const log = logger.child({ src: 'opensea' });
const BASE = 'https://api.opensea.io/api/v2';

const client = axios.create({
  baseURL: BASE,
  headers: { 'x-api-key': env.opensea.apiKey, accept: 'application/json' },
  timeout: 15_000,
});

export interface OpenSeaContract {
  address: string;
  chain: string;
}

export interface OpenSeaCollection {
  name: string;
  contracts: OpenSeaContract[];
}

export interface OpenSeaDropStage {
  uuid: string;
  stage_type: 'public_sale' | 'signed_presale' | string;
  label: string;
  price: string;
  start_time: string;
  end_time: string;
  max_per_wallet: string;
}

export interface OpenSeaDrop {
  collection_slug: string;
  collection_name: string;
  chain: string;
  contract_address: string;
  stages: OpenSeaDropStage[];
}

export interface OpenSeaMintAction {
  target: `0x${string}`;
  calldata: `0x${string}`;
  value: bigint;
}

/** Resolve a collection slug to its primary contract on the given chain. */
export async function getCollection(slug: string): Promise<OpenSeaCollection | null> {
  try {
    const res = await client.get(`/collections/${encodeURIComponent(slug)}`);
    const data = res.data as { name?: string; contracts?: OpenSeaContract[] };
    return { name: data.name ?? slug, contracts: data.contracts ?? [] };
  } catch (err) {
    log.warn({ err: (err as Error).message, slug }, 'getCollection failed');
    return null;
  }
}

/** Fetch primary-drop stages, including signed presales. */
export async function getDrop(slug: string): Promise<OpenSeaDrop | null> {
  try {
    const res = await client.get(`/drops/${encodeURIComponent(slug)}`);
    const data = res.data as Partial<OpenSeaDrop>;
    if (!data.contract_address || !Array.isArray(data.stages)) {
      throw new Error('OpenSea drop response is missing contract_address or stages');
    }
    return {
      collection_slug: data.collection_slug ?? slug,
      collection_name: data.collection_name ?? slug,
      chain: data.chain ?? 'ethereum',
      contract_address: data.contract_address,
      stages: data.stages,
    };
  } catch (err) {
    log.warn({ err: (err as Error).message, slug }, 'getDrop failed');
    return null;
  }
}

/**
 * Ask OpenSea to build the active-stage SeaDrop transaction. This is required
 * for signed presales because the calldata contains OpenSea's server signature.
 */
export async function buildDropMintTransaction(
  slug: string,
  minter: `0x${string}`,
  quantity: number,
): Promise<OpenSeaMintAction> {
  let res;
  try {
    res = await client.post(`/drops/${encodeURIComponent(slug)}/mint`, {
      minter,
      quantity,
    });
  } catch (err) {
    if (axios.isAxiosError(err)) {
      const status = err.response?.status ?? 'network';
      const retryAfter = err.response?.headers?.['retry-after'];
      const detail = JSON.stringify(err.response?.data ?? err.message).slice(0, 800);
      throw new Error(
        `OpenSea HTTP ${status}${retryAfter ? ` (retry-after ${retryAfter}s)` : ''}: ${detail}`,
      );
    }
    throw err;
  }
  const data = res.data as {
    target?: string;
    to?: string;
    calldata?: string;
    data?: string;
    value?: string;
  };
  const target = data.target ?? data.to;
  const calldata = data.calldata ?? data.data;
  if (!target?.match(/^0x[a-fA-F0-9]{40}$/)) {
    throw new Error('OpenSea mint action returned an invalid target');
  }
  if (!calldata?.match(/^0x[a-fA-F0-9]*$/)) {
    throw new Error('OpenSea mint action returned invalid calldata');
  }
  if (data.value == null || !/^\d+$/.test(String(data.value))) {
    throw new Error('OpenSea mint action returned an invalid value');
  }
  return {
    target: target as `0x${string}`,
    calldata: calldata as `0x${string}`,
    value: BigInt(data.value),
  };
}

/** Pick the contract address matching the target chain (or the first one). */
export function pickContract(
  collection: OpenSeaCollection,
  chain: ChainName,
): string | null {
  const match = collection.contracts.find((c) => c.chain === chain);
  return match?.address ?? collection.contracts[0]?.address ?? null;
}

/** Floor price in ETH from collection stats (used by the sell module in P2). */
export async function getFloorEth(slug: string): Promise<number | null> {
  try {
    const res = await client.get(`/collections/${encodeURIComponent(slug)}/stats`);
    const floor = (res.data as { total?: { floor_price?: number } })?.total?.floor_price;
    return typeof floor === 'number' ? floor : null;
  } catch (err) {
    log.warn({ err: (err as Error).message, slug }, 'getFloorEth failed');
    return null;
  }
}
