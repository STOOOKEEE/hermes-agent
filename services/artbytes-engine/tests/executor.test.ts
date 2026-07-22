import { cleanupDb } from './helpers/_setup.js';
import { test, describe, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { parseEther } from 'viem';
import {
  db,
  addMint,
  getMint,
  updateMintResolved,
  updateMintStatus,
  setMintBudget,
  type Mint,
} from '../src/db/index.js';
import * as budget from '../src/budget/index.js';
import { executeMint } from '../src/mint/executor.js';

const PRICE = parseEther('0.02');
const BUDGET = parseEther('0.1'); // AI-assigned per-mint budget, ample for price+gas
const FEE = '0x0000a26b00c1F0DF003000390027140000fAa719';
const CONTRACT = '0x00000000000000000000000000000000000000aa';
const ACCOUNT = { address: '0x1111111111111111111111111111111111111111' } as never;

function makeNotifier() {
  const calls: Record<string, number> = {};
  const rec = (k: string) => () => {
    calls[k] = (calls[k] ?? 0) + 1;
    return Promise.resolve();
  };
  return {
    spy: calls,
    obj: {
      mintT24h: rec('mintT24h'),
      mintLive: rec('mintLive'),
      approvalRequest: rec('approvalRequest'),
      mintDone: rec('mintDone'),
      mintFailed: rec('mintFailed'),
      manualMint: rec('manualMint'),
      error: rec('error'),
    } as never,
  };
}

function makeGate(approved: boolean) {
  const calls: Record<string, number> = {};
  return {
    spy: calls,
    obj: {
      isApproved: () => approved,
      request: () => {
        calls.request = (calls.request ?? 0) + 1;
        return Promise.resolve(false);
      },
    } as never,
  };
}

interface FakeOpts {
  simulateThrows?: boolean;
  gas?: bigint;
  baseFee?: bigint;
  receiptStatus?: 'success' | 'reverted';
  gasUsed?: bigint;
  effectiveGasPrice?: bigint;
}
function makePublicClient(o: FakeOpts) {
  return {
    readContract: async () => ({
      mintPrice: PRICE,
      startTime: 1000,
      endTime: 9999999999,
      maxTotalMintableByWallet: 5,
      feeBps: 500,
      restrictFeeRecipients: false,
    }),
    simulateContract: async () => {
      if (o.simulateThrows) throw new Error('execution reverted: exceeds max supply');
      return { request: {} };
    },
    call: async () => {
      if (o.simulateThrows) throw new Error('execution reverted: invalid signed mint');
      return { data: '0x' };
    },
    estimateContractGas: async () => o.gas ?? 21000n,
    estimateGas: async () => o.gas ?? 21000n,
    getBlock: async () => ({ baseFeePerGas: o.baseFee ?? 1_000_000_000n }),
    waitForTransactionReceipt: async () => ({
      status: o.receiptStatus ?? 'success',
      gasUsed: o.gasUsed ?? 21000n,
      effectiveGasPrice: o.effectiveGasPrice ?? 1_000_000_000n,
      blockNumber: 123n,
      logs: [],
    }),
  } as never;
}

const walletClient = {
  chain: { id: 1 },
  writeContract: async () => '0xabc0000000000000000000000000000000000000000000000000000000000000',
  sendTransaction: async () => '0xdef0000000000000000000000000000000000000000000000000000000000000',
} as never;

function seedMint(opts: { approved: boolean; budgetWei?: bigint; slug?: string }): Mint {
  const m = addMint({
    slug: opts.slug ?? `e-${Date.now()}-${Math.random()}`,
    chain: 'ethereum',
    decision: 'mint-1',
    qty_target: 1,
  });
  updateMintResolved(m.id, {
    contract: CONTRACT,
    mint_kind: 'seadrop_public',
    price_wei: PRICE,
    start_ts: 1000,
    end_ts: 9999999999,
    max_per_wallet: 5,
    fee_recipient: FEE,
  });
  if (opts.budgetWei != null) setMintBudget(m.id, opts.budgetWei);
  if (opts.approved) updateMintStatus(m.id, 'approved');
  return getMint(m.id)!;
}

beforeEach(() => {
  db.exec('DELETE FROM approvals; DELETE FROM txs; DELETE FROM positions; DELETE FROM budget_ledger; DELETE FROM mints;');
});
after(() => cleanupDb());

describe('mint executor — safety sequence (per-mint budget)', () => {
  test('not approved → requests approval, spends nothing', async () => {
    const m = seedMint({ approved: false, budgetWei: BUDGET });
    const n = makeNotifier();
    const g = makeGate(false);
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({}),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: g.obj,
    });
    assert.equal(res, null);
    assert.equal(g.spy.request, 1);
    assert.equal(budget.committedForMint(m.id), 0n);
  });

  test('no budget_wei → refused (capital-first), no reserve', async () => {
    const m = seedMint({ approved: true }); // no budget set
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({}),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
    });
    assert.equal(res, null);
    assert.equal(n.spy.mintFailed, 1);
    assert.equal(budget.committedForMint(m.id), 0n);
  });

  test('cost above the mint budget → refused, nothing committed', async () => {
    // budget 0.01 < cost (0.02 price + 0.01 gas) = 0.03
    const m = seedMint({ approved: true, budgetWei: parseEther('0.01') });
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({}),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
    });
    assert.equal(res, null);
    assert.equal(n.spy.mintFailed, 1);
    assert.equal(budget.committedForMint(m.id), 0n);
  });

  test('simulate revert → abort, budget released, status failed', async () => {
    const m = seedMint({ approved: true, budgetWei: BUDGET });
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({ simulateThrows: true }),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
    });
    assert.equal(res, null);
    assert.equal(getMint(m.id)?.status, 'failed');
    assert.equal(n.spy.mintFailed, 1);
    assert.equal(budget.committedForMint(m.id), 0n, 'reservation must be released');
  });

  test('gas above MAX_GAS_ETH → skip, budget released', async () => {
    const m = seedMint({ approved: true, budgetWei: BUDGET });
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({ gas: 10_000_000n, baseFee: 1_000_000_000_000n }),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
    });
    assert.equal(res, null);
    assert.equal(n.spy.mintFailed, 1);
    assert.equal(budget.committedForMint(m.id), 0n, 'reservation released on gas skip');
  });

  test('success → status minted, actual spend recorded, mintDone notified', async () => {
    const m = seedMint({ approved: true, budgetWei: BUDGET });
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({ receiptStatus: 'success', gasUsed: 21000n, effectiveGasPrice: 1_000_000_000n }),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
    });
    assert.ok(res, 'should return a result');
    assert.equal(getMint(m.id)?.status, 'minted');
    assert.equal(n.spy.mintDone, 1);
    const expectedSpend = PRICE + 21000n * 1_000_000_000n;
    assert.equal(budget.spentForMint(m.id), expectedSpend);
    // commit − refund = the spent amount (unused reservation refunded)
    assert.equal(budget.committedForMint(m.id), expectedSpend);
  });

  test('signed presale → uses OpenSea mint action, simulates raw calldata, and respects budget', async () => {
    const m = addMint({
      slug: 'signed-presale',
      chain: 'ethereum',
      decision: 'mint-1',
      qty_target: 1,
    });
    updateMintResolved(m.id, {
      contract: CONTRACT,
      mint_kind: 'seadrop_allowlist',
      price_wei: PRICE,
      start_ts: 1000,
      end_ts: 9999999999,
      max_per_wallet: 1,
      fee_recipient: null,
    });
    setMintBudget(m.id, BUDGET);
    updateMintStatus(m.id, 'approved');
    let builderCalls = 0;
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({}),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
      buildOpenSeaMint: async (slug, minter, quantity) => {
        builderCalls += 1;
        assert.equal(slug, 'signed-presale');
        assert.equal(minter, ACCOUNT.address);
        assert.equal(quantity, 1);
        return {
          target: '0x00005EA00Ac477B1030CE78506496e8C2dE24bf5',
          calldata: '0x1234',
          value: PRICE,
        };
      },
    });
    assert.ok(res);
    assert.equal(builderCalls, 1);
    assert.equal(getMint(m.id)?.status, 'minted');
    assert.equal(n.spy.mintDone, 1);
  });

  test('per-mint gas_plan overrides env: tiny max_gas_eth → skip', async () => {
    // gas_plan caps gas at 0.0000001 ETH; the estimated cost will exceed it.
    const m = seedMint({ approved: true, budgetWei: BUDGET });
    db.prepare('UPDATE mints SET gas_plan = ? WHERE id = ?').run(
      JSON.stringify({ priorityGwei: 5, maxGasEth: 0.0000001 }),
      m.id,
    );
    const n = makeNotifier();
    const res = await executeMint(m.id, {
      publicClient: makePublicClient({ gas: 100000n, baseFee: 50_000_000_000n }),
      walletClient,
      account: ACCOUNT,
      notifier: n.obj,
      gate: makeGate(true).obj,
    });
    assert.equal(res, null);
    assert.equal(n.spy.mintFailed, 1, 'per-mint gas ceiling should trigger a skip');
    assert.equal(budget.committedForMint(m.id), 0n);
  });

  test('idempotence → second call does not mint again', async () => {
    const m = seedMint({ approved: true, budgetWei: BUDGET });
    const n = makeNotifier();
    await executeMint(m.id, {
      publicClient: makePublicClient({}), walletClient, account: ACCOUNT,
      notifier: n.obj, gate: makeGate(true).obj,
    });
    const before = budget.spent();
    const res2 = await executeMint(m.id, {
      publicClient: makePublicClient({}), walletClient, account: ACCOUNT,
      notifier: n.obj, gate: makeGate(true).obj,
    });
    assert.equal(res2, null);
    assert.equal(budget.spent(), before, 'no second spend');
  });
});
