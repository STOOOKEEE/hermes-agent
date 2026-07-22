import { cleanupDb } from './helpers/_setup.js';
import { test, describe, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { parseEther } from 'viem';
import { db } from '../src/db/index.js';
import * as budget from '../src/budget/index.js';
import { BudgetExceededError } from '../src/budget/index.js';

beforeEach(() => {
  db.exec('DELETE FROM budget_ledger;');
});

after(() => cleanupDb());

describe('budget ledger — per-mint budget (AI-defined, no global cap)', () => {
  test('starts empty', () => {
    assert.equal(budget.committed(), 0n);
    assert.equal(budget.spent(), 0n);
    assert.equal(budget.committedForMint(1), 0n);
  });

  test('reserve within the mint budget commits; refund releases it', () => {
    const mintBudget = parseEther('0.1');
    budget.reserve(1, 'W_raid', parseEther('0.06'), mintBudget);
    assert.equal(budget.committedForMint(1), parseEther('0.06'));
    budget.refund(1, 'W_raid', parseEther('0.06'));
    assert.equal(budget.committedForMint(1), 0n);
  });

  test('reserve up to exactly the mint budget is allowed', () => {
    const mintBudget = parseEther('0.1');
    budget.reserve(1, 'W_raid', mintBudget, mintBudget);
    assert.equal(budget.committedForMint(1), mintBudget);
  });

  test('reserve over the mint budget (even by 1 wei) is refused, writes nothing', () => {
    const mintBudget = parseEther('0.1');
    assert.throws(
      () => budget.reserve(1, 'W_raid', mintBudget + 1n, mintBudget),
      BudgetExceededError,
    );
    assert.equal(budget.committedForMint(1), 0n);
  });

  test('budgets are independent per mint', () => {
    budget.reserve(1, 'W_raid', parseEther('0.08'), parseEther('0.1'));
    // mint 2 has its own (smaller) budget; mint 1 usage does not affect it.
    assert.throws(
      () => budget.reserve(2, 'W_krysko', parseEther('0.06'), parseEther('0.05')),
      BudgetExceededError,
    );
    budget.reserve(2, 'W_krysko', parseEther('0.05'), parseEther('0.05'));
    assert.equal(budget.committedForMint(2), parseEther('0.05'));
  });

  test('spend is tracked; proceeds are informational only', () => {
    budget.reserve(1, 'W_raid', parseEther('0.1'), parseEther('0.1'));
    budget.spend(1, 'W_raid', parseEther('0.08'));
    budget.refund(1, 'W_raid', parseEther('0.02'));
    assert.equal(budget.spent(), parseEther('0.08'));
    assert.equal(budget.spentForMint(1), parseEther('0.08'));

    budget.recordProceeds(1, 'W_raid', parseEther('1'));
    assert.equal(budget.proceeds(), parseEther('1'));
  });

  test('check is a dry per-mint test', () => {
    const mintBudget = parseEther('0.1');
    assert.equal(budget.check(1, parseEther('0.1'), mintBudget), true);
    assert.equal(budget.check(1, parseEther('0.1') + 1n, mintBudget), false);
  });
});
