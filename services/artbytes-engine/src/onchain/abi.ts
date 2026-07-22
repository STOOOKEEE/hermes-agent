import { parseAbi } from 'viem';

/**
 * OpenSea SeaDrop (ERC721SeaDrop) minimal ABI — public mint + drop config read.
 * SeaDrop contract: 0x00005EA00Ac477B1030CE78506496e8C2dE24bf5
 */
export const SEADROP_ABI = parseAbi([
  'struct PublicDrop { uint80 mintPrice; uint48 startTime; uint48 endTime; uint16 maxTotalMintableByWallet; uint16 feeBps; bool restrictFeeRecipients; }',
  'function getPublicDrop(address nftContract) view returns (PublicDrop)',
  'function mintPublic(address nftContract, address feeRecipient, address minterIfNotPayer, uint256 quantity) payable',
]);

/** ERC721 Transfer event — used to extract minted token ids from the receipt. */
export const ERC721_ABI = parseAbi([
  'event Transfer(address indexed from, address indexed to, uint256 indexed tokenId)',
]);

export interface PublicDrop {
  mintPrice: bigint;
  startTime: number;
  endTime: number;
  maxTotalMintableByWallet: number;
  feeBps: number;
  restrictFeeRecipients: boolean;
}
