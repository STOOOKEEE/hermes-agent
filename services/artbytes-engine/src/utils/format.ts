import { getAddress, isAddress, formatEther, parseEther, type Address } from 'viem';

/** Checksum an address; falls back to lowercase if it cannot be parsed. */
export function normalizeAddress(addr: string): string {
  try {
    return getAddress(addr);
  } catch {
    return addr.toLowerCase();
  }
}

export function isValidAddress(addr: string): addr is Address {
  return isAddress(addr);
}

export function shortAddress(addr: string): string {
  const a = normalizeAddress(addr);
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

/** wei (bigint) -> human ETH string, trimmed to a readable precision. */
export function weiToEth(wei: bigint): string {
  return formatEth(Number(formatEther(wei)));
}

/** ETH string (e.g. "0.05") -> wei bigint. */
export function ethToWei(eth: string): bigint {
  return parseEther(eth);
}

export function formatEth(n: number): string {
  if (n === 0) return '0';
  if (n < 0.001) return n.toExponential(2);
  if (n < 1) return n.toFixed(4);
  if (n < 100) return n.toFixed(3);
  return n.toFixed(2);
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** JSON replacer that serializes BigInt values (e.g. wei amounts) as strings. */
export function bigintReplacer(_key: string, value: unknown): unknown {
  return typeof value === 'bigint' ? value.toString() : value;
}
