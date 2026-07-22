# artbytes-engine

Onchain bot that **executes** ArtBytes NFT mints (OpenSea SeaDrop) under a hard
budget cap + human approval, then manages the exit. **Hermes decides what to
mint, the engine executes** — capital-first: everything is capped, everything
needs approval before spending, everything is cancellable.

See [`SPEC.md`](./SPEC.md) for the full spec. The engine supports SeaDrop public
stages and OpenSea server-signed presales end-to-end. Signed presales use
OpenSea's official drop-mint transaction API; every action is still simulated,
budgeted and explicitly approved before it is sent.

## Stack
Node 20 + TypeScript (ESM) · `viem` (onchain, `simulateContract` + EIP-1559) ·
`better-sqlite3` (state) · `grammy` (Telegram) · `pino` (logs).

## Setup
```bash
npm install
cp .env.example .env   # fill RPC + OpenSea key/file + PK file paths
npm run build
npm test               # unit suite — budget cap, DAO, executor (no real ETH)
```

Private keys live **on the VM only**, in chmod-600 files referenced by
`WALLET_RAID_PK_FILE` / `WALLET_KRYSKO_PK_FILE`. They are never committed,
logged, or stored in the DB.

### Gas
Gas is tunable two ways:
- **Globally** via env — `MAX_GAS_ETH` (absolute cost ceiling per TX, anti
  gas-war), `MAX_PRIORITY_GWEI` (validator tip), `BASEFEE_MULT`
  (`maxFeePerGas = baseFee × mult + priority`).
- **Per mint** — the AI sets a `gas_plan` in the order (`priorityGwei`,
  `baseFeeMult`, `maxGasEth`); any field overrides the env default for that mint,
  the rest fall back. Use it to crank the tip on a competitive T-0 drop without
  touching the global config.

## Usage
```bash
# Add a mint order (Hermes / you), resolve its SeaDrop config:
node dist/control/cli.js mint:add --slug <slug> --chain ethereum \
     --decision mint-1 --qty 1 --budget 0.05 --wallet W_raid \
     --priority-gwei 3 --basefee-mult 3 --max-gas-eth 0.02   # optional per-mint gas override

# Select a signed OpenSea presale by its exact label:
node dist/control/cli.js mint:add --slug <slug> --stage "GTD" --chain ethereum \
     --decision mint-1 --qty 1 --budget 0.006 --wallet W_raid \
     --max-gas-eth 0.001 --fallback-public

node dist/control/cli.js status          # budget + mints (JSON)
node dist/control/cli.js approve <id>     # approve a mint
node dist/control/cli.js cancel <id>      # cancel + release budget

npm start                                 # run the engine monitor
```

Telegram is optional. When configured, `/status`, `/mints`, `/approve <id>` and
`/cancel <id>` plus inline buttons remain available. Without Telegram, use the
CLI to approve before the scheduled start; the monitor continues headlessly.

## Flow
`mint:add` → `resolve` (slug → contract + drop config) → `monitor` (T-24h notice,
live detection) → **approval gate** (Telegram or CLI) → `mint executor`
(idempotence → re-resolve → reserve budget → **simulate** → gas ceiling →
write → receipt → ledger + positions) → notify.

## Safety invariants (hard rules)
1. **Budgets are set by the AI per mint** (`budget_wei` in the order). The engine
   carries no hard-coded global cap; it refuses if cost (mint + gas) exceeds that
   mint's budget — not by 1 wei — and refuses outright if a mint has no budget.
2. No spend without an approval (per-mint `AUTO_APPROVE` opt-in only, off by default).
3. Always simulate before sending; simulated revert ⇒ abort.
4. Cancellable at any time (pre-mint: cancel + refund; post-send: switch to exit).
5. Absolute gas ceiling per TX (`MAX_GAS_ETH`).
6. Idempotence — never two mints for the same `mint_id`.
7. Keys never leave the VM (read from chmod-600 files).
8. Capital-first — on doubt / timeout / missing data, do not mint.

## Testing
Unit tests run with the Node test runner (no network, no real ETH):
```bash
npm test
```
The budget ledger (`tests/budget.test.ts`) is the most important suite — it
proves the cap is unbreachable.

**Manual on-chain dry run** (optional, outside the unit suite): simulate against
an anvil fork — `simulateContract` is read-only, so this never spends:
```bash
anvil --fork-url $ETH_RPC_URL
# then point ETH_RPC_URL at http://127.0.0.1:8545 and add/approve a known drop.
```

## Deployment
Docker, alongside the watcher on the Hermes VM:
```bash
docker compose up -d --build
```
The compose file runs as uid 1001 (hermes), mounts `./data` for the SQLite DB,
and mounts `~/.hermes/secrets` read-only for the PK files.

## Roadmap (not yet implemented)
- **Phase 2 — exit**: `sell/` with an abstract `Lister` (OpenSea/Seaport;
  Reservoir is dead; Blur read-only; Verse TBD), floor tracking, derisk,
  closed-loop relisting.
- **Phase 3 — autonomy**: custom-contract resolver,
  tightened T-5min polling, `control/` HTTP API (bearer) for Hermes, codex
  go/no-go advice, Base end-to-end.
