import { parseEther } from 'viem';
import { env } from './env.js';
import { logger } from '../utils/logger.js';

const log = logger.child({ src: 'gas' });

/** Per-mint gas override the AI can set in the order. All fields optional. */
export interface GasPlan {
  /** maxPriorityFeePerGas in gwei (the validator tip). */
  priorityGwei?: number;
  /** maxFeePerGas = baseFee * baseFeeMult + priority. */
  baseFeeMult?: number;
  /** absolute gas cost ceiling for this mint, in ETH. */
  maxGasEth?: number;
}

export interface EffectiveGas {
  maxPriorityGwei: number;
  baseFeeMult: number;
  maxGasWei: bigint;
}

/** Merge a per-mint gas plan over the global env defaults. */
export function resolveGas(plan?: GasPlan | null): EffectiveGas {
  return {
    maxPriorityGwei: plan?.priorityGwei ?? env.gas.maxPriorityGwei,
    baseFeeMult: plan?.baseFeeMult ?? env.gas.baseFeeMult,
    maxGasWei:
      plan?.maxGasEth != null ? ethNumberToWei(plan.maxGasEth) : env.gas.maxGasWei,
  };
}

/** parseEther rejects scientific notation (e.g. "1e-7"); fix to 18 decimals first. */
function ethNumberToWei(eth: number): bigint {
  return parseEther(eth.toFixed(18));
}

/** Parse the stored gas_plan JSON, tolerating null/garbage. */
export function parseGasPlan(json: string | null | undefined): GasPlan | null {
  if (!json) return null;
  try {
    const p = JSON.parse(json) as GasPlan;
    return {
      priorityGwei: numOrUndef(p.priorityGwei),
      baseFeeMult: numOrUndef(p.baseFeeMult),
      maxGasEth: numOrUndef(p.maxGasEth),
    };
  } catch {
    log.warn({ json }, 'invalid gas_plan JSON — ignoring');
    return null;
  }
}

function numOrUndef(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}
