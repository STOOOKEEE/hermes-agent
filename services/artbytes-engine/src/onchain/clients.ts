import {
  createPublicClient,
  createWalletClient,
  http,
  type PublicClient,
  type WalletClient,
  type Account,
} from 'viem';
import { getChain, getChainByNumericId, type ChainId } from '../config/chains.js';

const publicClients = new Map<number, PublicClient>();

/** Cached public (read) client for a chain. */
export function publicClientFor(chain: ChainId | number): PublicClient {
  const cfg = typeof chain === 'number' ? getChainByNumericId(chain) : getChain(chain);
  const cached = publicClients.get(cfg.chainId);
  if (cached) return cached;
  if (!cfg.rpcUrl) throw new Error(`No RPC URL configured for chain ${cfg.id}`);
  const client = createPublicClient({
    chain: cfg.viemChain,
    transport: http(cfg.rpcUrl),
  }) as PublicClient;
  publicClients.set(cfg.chainId, client);
  return client;
}

/** Wallet (write) client bound to a specific account on a chain. */
export function walletClientFor(chain: ChainId | number, account: Account): WalletClient {
  const cfg = typeof chain === 'number' ? getChainByNumericId(chain) : getChain(chain);
  if (!cfg.rpcUrl) throw new Error(`No RPC URL configured for chain ${cfg.id}`);
  return createWalletClient({
    account,
    chain: cfg.viemChain,
    transport: http(cfg.rpcUrl),
  });
}

export interface GasEstimate {
  gas: bigint;
  maxFeePerGas: bigint;
  maxPriorityFeePerGas: bigint;
  /** Worst-case total gas cost = gas * maxFeePerGas. */
  maxCostWei: bigint;
}

/**
 * Build EIP-1559 fee params from current base fee:
 *   maxFeePerGas = baseFee * baseFeeMult + maxPriorityFeePerGas
 */
export async function buildGasParams(
  client: PublicClient,
  gas: bigint,
  opts: { maxPriorityGwei: number; baseFeeMult: number },
): Promise<GasEstimate> {
  const block = await client.getBlock({ blockTag: 'latest' });
  const baseFee = block.baseFeePerGas ?? 0n;
  const maxPriorityFeePerGas = BigInt(Math.round(opts.maxPriorityGwei * 1e9));
  const maxFeePerGas =
    baseFee * BigInt(Math.max(1, Math.round(opts.baseFeeMult))) + maxPriorityFeePerGas;
  return {
    gas,
    maxFeePerGas,
    maxPriorityFeePerGas,
    maxCostWei: gas * maxFeePerGas,
  };
}
