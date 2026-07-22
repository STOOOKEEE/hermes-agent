import { mainnet, base, type Chain } from 'viem/chains';
import { env } from './env.js';

export type ChainId = 'ethereum' | 'base';

export interface ChainConfig {
  id: ChainId;
  name: string;
  chainId: number;
  viemChain: Chain;
  rpcUrl: string;
  explorerUrl: string;
  openseaSlug: 'ethereum' | 'base';
}

export const CHAINS: Record<ChainId, ChainConfig> = {
  ethereum: {
    id: 'ethereum',
    name: 'Ethereum',
    chainId: 1,
    viemChain: mainnet,
    rpcUrl: env.eth.rpcUrl,
    explorerUrl: 'https://etherscan.io',
    openseaSlug: 'ethereum',
  },
  base: {
    id: 'base',
    name: 'Base',
    chainId: 8453,
    viemChain: base,
    rpcUrl: env.base.rpcUrl,
    explorerUrl: 'https://basescan.org',
    openseaSlug: 'base',
  },
};

export function getChain(id: ChainId): ChainConfig {
  const c = CHAINS[id];
  if (!c) throw new Error(`Unknown chain: ${id}`);
  return c;
}

export function getChainByNumericId(chainId: number): ChainConfig {
  const c = Object.values(CHAINS).find((x) => x.chainId === chainId);
  if (!c) throw new Error(`Unknown numeric chainId: ${chainId}`);
  return c;
}

/**
 * OpenSea SeaDrop (ERC721SeaDrop) — same address on Ethereum and Base.
 * https://etherscan.io/address/0x00005EA00Ac477B1030CE78506496e8C2dE24bf5
 */
export const SEADROP_ADDRESS =
  '0x00005EA00Ac477B1030CE78506496e8C2dE24bf5' as const;

export function explorerTxUrl(chain: ChainConfig, hash: string): string {
  return `${chain.explorerUrl}/tx/${hash}`;
}

export function explorerAddressUrl(chain: ChainConfig, address: string): string {
  return `${chain.explorerUrl}/address/${address}`;
}

export function openseaAssetUrl(
  chain: ChainConfig,
  contract: string,
  tokenId: string,
): string {
  return `https://opensea.io/assets/${chain.openseaSlug}/${contract}/${tokenId}`;
}
