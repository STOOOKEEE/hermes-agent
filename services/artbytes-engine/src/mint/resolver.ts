import { SEADROP_ADDRESS } from '../config/chains.js';
import { SEADROP_ABI } from '../onchain/abi.js';
import type { Mint } from '../db/index.js';

export interface MintCall {
  address: `0x${string}`;
  abi: typeof SEADROP_ABI;
  functionName: 'mintPublic';
  args: readonly [`0x${string}`, `0x${string}`, `0x${string}`, bigint];
  value: bigint;
}

/**
 * Build the on-chain call for a resolved mint. Phase 1 supports SeaDrop public.
 *   mintPublic(nftContract, feeRecipient, minterIfNotPayer, quantity) payable
 *   value = price_wei * quantity
 */
export function buildMintCall(mint: Mint, minter: `0x${string}`, qty: number): MintCall {
  if (mint.mint_kind !== 'seadrop_public') {
    throw new Error(`buildMintCall: unsupported mint_kind "${mint.mint_kind}" (P1: seadrop_public only)`);
  }
  if (!mint.contract) throw new Error('buildMintCall: mint has no resolved contract');
  if (!mint.fee_recipient) throw new Error('buildMintCall: mint has no fee_recipient');
  if (mint.price_wei == null) throw new Error('buildMintCall: mint has no price_wei');

  const nftContract = mint.contract as `0x${string}`;
  const feeRecipient = mint.fee_recipient as `0x${string}`;
  const price = BigInt(mint.price_wei);
  const quantity = BigInt(qty);

  return {
    address: SEADROP_ADDRESS,
    abi: SEADROP_ABI,
    functionName: 'mintPublic',
    args: [nftContract, feeRecipient, minter, quantity],
    value: price * quantity,
  };
}
