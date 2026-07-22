import { cleanupDb } from './helpers/_setup.js';
import { test, describe, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { parseEther } from 'viem';
import {
  db,
  addMint,
  getMint,
  getMintBySlug,
  updateMintResolved,
  updateMintStatus,
  insertTx,
  hasMintTx,
  createApproval,
  decideApproval,
  getPendingApproval,
  expireStaleApprovals,
} from '../src/db/index.js';

beforeEach(() => {
  db.exec('DELETE FROM approvals; DELETE FROM txs; DELETE FROM positions; DELETE FROM budget_ledger; DELETE FROM mints;');
});

after(() => cleanupDb());

describe('mints DAO', () => {
  test('addMint then getMintBySlug round-trips, status defaults to watching', () => {
    const m = addMint({ slug: 'glifs', chain: 'ethereum', decision: 'mint-1', qty_target: 2 });
    assert.equal(m.status, 'watching');
    assert.equal(m.qty_target, 2);
    const found = getMintBySlug('glifs');
    assert.equal(found?.id, m.id);
  });

  test('slug uniqueness is enforced', () => {
    addMint({ slug: 'dup', chain: 'ethereum' });
    assert.throws(() => addMint({ slug: 'dup', chain: 'ethereum' }));
  });

  test('updateMintResolved stores price as wei string', () => {
    const m = addMint({ slug: 'res', chain: 'ethereum' });
    updateMintResolved(m.id, {
      contract: '0x00000000000000000000000000000000000000aa',
      mint_kind: 'seadrop_public',
      price_wei: parseEther('0.02'),
      start_ts: 1000,
      end_ts: 2000,
      max_per_wallet: 3,
      fee_recipient: '0x00000000000000000000000000000000000000bb',
    });
    const r = getMint(m.id)!;
    assert.equal(r.mint_kind, 'seadrop_public');
    assert.equal(r.price_wei, parseEther('0.02').toString());
    assert.equal(r.max_per_wallet, 3);
  });
});

describe('idempotence guards', () => {
  test('hasMintTx is false until a non-reverted mint tx exists', () => {
    const m = addMint({ slug: 'idem', chain: 'ethereum' });
    assert.equal(hasMintTx(m.id), false);

    insertTx({ mint_id: m.id, kind: 'mint', status: 'reverted', hash: '0xrev' });
    assert.equal(hasMintTx(m.id), false, 'reverted tx does not count');

    insertTx({ mint_id: m.id, kind: 'mint', status: 'pending', hash: '0xpending' });
    assert.equal(hasMintTx(m.id), true);
  });

  test('status transitions persist', () => {
    const m = addMint({ slug: 'st', chain: 'ethereum' });
    updateMintStatus(m.id, 'approved');
    assert.equal(getMint(m.id)?.status, 'approved');
    updateMintStatus(m.id, 'minted');
    assert.equal(getMint(m.id)?.status, 'minted');
  });
});

describe('approvals', () => {
  test('createApproval -> pending -> approved', () => {
    const m = addMint({ slug: 'ap', chain: 'ethereum' });
    createApproval({ mint_id: m.id, kind: 'mint', payload: { qty: 1 } });
    assert.ok(getPendingApproval(m.id, 'mint'));
    const ok = decideApproval(m.id, 'mint', 'approved');
    assert.equal(ok, true);
    assert.equal(getPendingApproval(m.id, 'mint'), undefined);
  });

  test('expireStaleApprovals cancels old pendings', () => {
    const m = addMint({ slug: 'exp', chain: 'ethereum' });
    createApproval({ mint_id: m.id, kind: 'mint' });
    // Force the approval to look 3h old.
    db.prepare(
      `UPDATE approvals SET requested_at = ? WHERE mint_id = ?`,
    ).run(Math.floor(Date.now() / 1000) - 3 * 3600, m.id);

    const expired = expireStaleApprovals(2 * 3600 * 1000); // 2h timeout
    assert.equal(expired, 1);
    assert.equal(getPendingApproval(m.id, 'mint'), undefined);
  });
});
