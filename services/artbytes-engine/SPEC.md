# artbytes-engine — Spec du bot de mint + sell

Bot onchain qui exécute les mints NFT décidés par la stratégie ArtBytes (Hermes/skill
`artbytes-mint-hunter`) puis gère la sortie. **Hermes décide, l'engine exécute.**

Philosophie : **capital-first**. Mieux vaut rater un mint que perdre. Tout est plafonné,
tout passe par une approbation humaine avant de dépenser, tout est annulable.

---

## 0. Périmètre

L'engine NE décide PAS quoi minter (c'est le rôle du skill + watchlist + hype-board).
L'engine reçoit un **ordre de mint validé** et se charge de :

1. Résoudre l'adresse du contrat + la config de mint d'un projet
2. Surveiller le moment du mint (détection T-0) + notifier T-24h
3. Construire, simuler, envoyer la TX de mint (SeaDrop / contrat custom / fallback)
4. Faire respecter le cap budget (0.25 ETH global) et le gate d'approbation
5. Après mint : suivre le floor et gérer la sortie (derisk adaptatif)
6. Notifier chaque étape sur Telegram, rester annulable à tout instant

Chains : **Ethereum + Base**. Standard cible : **OpenSea SeaDrop** (ERC721SeaDrop).
Contrats custom gérés via resolver. Verse = à éviter (cf skill).

---

## 1. Stack technique recommandée

| Brique | Choix | Raison |
|---|---|---|
| Langage | **TypeScript** (Node 20+) | cohérent avec le watcher existant |
| Lib onchain | **viem** | type-safe, simulation native (`simulateContract`), gas EIP-1559 propre |
| Wallets | `privateKeyToAccount` (viem) | clés locales sur la VM, chmod 600 |
| DB | **better-sqlite3** | déjà utilisé dans `~/dev/NFT/watcher` |
| Floor / orders | **Reservoir API** (gratuit) | floor, bids, listings multi-marketplace, + Base |
| Listing / vente | **Seaport 1.6** (contrat direct) ou Reservoir execute | poster des asks |
| Données mint | OpenSea API v2 + lecture contrat SeaDrop | slug → contrat, config drop |
| Prix ETH | watcher existant ou Coingecko | valoriser le PnL |
| Notifs / contrôle | **Telegram** (réutiliser le bot Hermes) | approbation + annulation |
| Décision IA (option) | **codex gateway** `:8742` | avis go/no-go, sizing |

Le watcher (`~/dev/NFT/watcher`, événements OpenSea) sert de **source de données prix/floor**
en lecture (`docker exec nft-watcher node dist/query.js ...`).

---

## 2. Architecture (modules)

```
artbytes-engine/
├── src/
│   ├── config/        env, chains (eth+base), addresses SeaDrop/Seaport
│   ├── db/            schema sqlite + DAO (mints, txs, positions, budget)
│   ├── wallets/       chargement clés, abstraction signer (W_raid, W_krysko)
│   ├── resolve/       slug OpenSea -> contrat + config drop (price/start/type)
│   ├── monitor/       boucle de veille : détecte mint live, planifie T-24h
│   ├── mint/          resolver de stratégie + executor (simulate -> send)
│   ├── budget/        garde-fou cap 0.25 ETH (committed + spent)
│   ├── approval/      gate Telegram (pending -> approved/cancelled)
│   ├── sell/          floor watch + derisk + listing Seaport
│   ├── notify/        messages Telegram lisibles
│   └── control/       CLI + petit serveur HTTP local (pilotage par Hermes)
├── data/engine.db
└── SPEC.md
```

Flux : `watchlist (skill)` → `resolve` → `monitor` → (T-24h notif) → `approval gate`
→ `mint executor` → `position ouverte` → `sell/derisk` → `clôture + PnL`.

---

## 3. Modèle de données (SQLite)

```sql
-- un projet/mint suivi
CREATE TABLE mints (
  id            INTEGER PRIMARY KEY,
  slug          TEXT UNIQUE,           -- slug opensea
  name          TEXT,
  chain         TEXT,                  -- 'ethereum' | 'base'
  contract      TEXT,                  -- 0x... (résolu)
  mint_kind     TEXT,                  -- 'seadrop_public' | 'seadrop_allowlist' | 'custom' | 'manual'
  price_wei     TEXT,                  -- prix unitaire (string bigint)
  start_ts      INTEGER,               -- début mint (unix)
  wl_wallet     TEXT,                  -- 'W_raid' | 'W_krysko' | null
  wl_proof      TEXT,                  -- JSON proof allowlist si besoin
  decision      TEXT,                  -- 'skip'|'watch'|'mint-1'|'mint-conviction'|'max'
  qty_target    INTEGER,               -- nb à minter (conviction)
  budget_wei    TEXT,                  -- budget alloué à ce mint
  status        TEXT,                  -- watching|approved|minting|minted|failed|sold|passed
  exit_plan     TEXT,                  -- JSON: {flipPct, derisk, moonbag}
  created_at    INTEGER, updated_at INTEGER
);

-- chaque transaction onchain
CREATE TABLE txs (
  id INTEGER PRIMARY KEY, mint_id INTEGER, kind TEXT,  -- 'mint'|'approve'|'list'|'cancel'|'sale'
  wallet TEXT, hash TEXT, status TEXT,                 -- pending|confirmed|reverted
  value_wei TEXT, gas_wei TEXT, block INTEGER, ts INTEGER
);

-- position post-mint (inventaire détenu)
CREATE TABLE positions (
  id INTEGER PRIMARY KEY, mint_id INTEGER, token_id TEXT, wallet TEXT,
  cost_wei TEXT, listed_wei TEXT, sold_wei TEXT,
  status TEXT,                                          -- held|listed|sold
  ts INTEGER
);

-- approbations humaines
CREATE TABLE approvals (
  id INTEGER PRIMARY KEY, mint_id INTEGER, kind TEXT,  -- 'mint'|'sell'
  requested_at INTEGER, decided_at INTEGER,
  decision TEXT,                                        -- pending|approved|cancelled
  payload TEXT                                          -- détails montrés à l'humain
);

-- ledger budget (vérité unique du cap)
CREATE TABLE budget_ledger (
  id INTEGER PRIMARY KEY, mint_id INTEGER, wallet TEXT,
  kind TEXT,                                            -- 'commit'|'spend'|'refund'|'proceeds'
  amount_wei TEXT, ts INTEGER, note TEXT
);
```

---

## 4. Modules — specs détaillées

### 4.1 `resolve/` — slug → contrat + config
Entrée : slug OpenSea (ex `glifs`) + chain.
- OpenSea API v2 `GET /chain/{chain}/contract` / `GET /collections/{slug}` → adresse contrat.
- Lecture onchain pour identifier le type :
  - SeaDrop : le contrat NFT expose `getPublicDrop(address seadrop)` / events `SeaDropTokenDeployed`. Le **SeaDrop** mainnet = `0x00005EA00Ac477B1030CE78506496e8C2dE24bf5`.
  - Sinon, custom : récupérer l'ABI (Etherscan/Basescan API) et repérer une fonction de mint (`mint`, `mintPublic`, `claim`, `publicMint`, `purchase`).
- Sortie : `{contract, mint_kind, price_wei, start_ts, max_per_wallet}`.
- Si rien de fiable → `mint_kind='manual'` (mint via front-end, humain dans la boucle).

### 4.2 `monitor/` — détection T-0 + alerte T-24h
Boucle (cron 1-5 min) sur les `mints` en `status in (watching, approved)` :
- Si `start_ts - now ≈ 24h` (±tolérance) et pas déjà notifié → **notif T-24h** Telegram (demande de confirmation du plan + budget).
- À l'approche de `start_ts` (T-5min), resserrer la boucle (polling 5-10s) et tester si le mint est **live** :
  - SeaDrop : `getPublicDrop().startTime <= now <= endTime` et `maxTotalMintableByWallet > 0`.
  - custom : appeler une vue (`saleIsActive`, `mintActive`) si dispo, sinon simuler un mint dry (`simulateContract`) → succès = live.
- Mint live + `status=approved` → déclenche `mint/executor`.

### 4.3 `mint/` — resolver de stratégie + executor
**Resolver** : selon `mint_kind`, construit l'appel :
- `seadrop_public` → SeaDrop `mintPublic(nftContract, feeRecipient, minterIfNotPayer, quantity)` **payable** (`value = price_wei * quantity`). `feeRecipient` = lu depuis la config drop, `minterIfNotPayer = wallet`.
- `seadrop_allowlist` → `mintAllowList(nftContract, feeRecipient, minter, quantity, mintParams, proof)` (proof = Merkle, stocké dans `wl_proof`).
- `custom` → encode la fonction repérée (`mint(qty)` / `claim(...)`) avec `value`.
- `manual` → n'exécute pas : envoie une notif "mint à faire à la main MAINTENANT" avec le lien.

**Executor** (séquence stricte) :
1. `budget.check(mint)` → refuse si dépasse le cap.
2. `approval.require('mint', mint)` → doit être `approved` (sinon stop).
3. **`simulateContract`** (viem) → si revert, abort + notif (raison).
4. Gas EIP-1559 : `maxPriorityFeePerGas` configurable, `maxFeePerGas = baseFee*mult + priority`. Plafond gas absolu en ETH (`MAX_GAS_ETH`).
5. `writeContract` → hash → enregistre `txs(pending)`.
6. `waitForTransactionReceipt` → confirmé/reverted → maj DB + `budget_ledger(spend)` + crée `positions` (récupère les `token_id` mintés depuis les logs Transfer).
7. Notif résultat (tokens reçus, coût total gas inclus).

Idempotence : un mint en `minting`/`minted` ne se re-déclenche jamais. Un seul mint par `mint_id` (sauf qty>1 en une TX).

### 4.4 `budget/` — le cap 0.25 ETH (invariant dur)
- `budget_ledger` = vérité unique. `committed = Σ commit - Σ refund` ; `spent = Σ spend`.
- `available = CAP_WEI - committed`. **Toute** demande de mint réserve d'abord (`commit`), exécute (`spend`), libère le reste (`refund`).
- `CAP_WEI = 0.25 ETH`, **tous wallets confondus** (W_raid + W_krysko).
- Refus strict si `cost (mint + gas estimé) > available`. Jamais de dépassement, même d'1 wei.

### 4.5 `approval/` — gate humain + annulation
- Avant tout `spend`, créer `approvals(pending)` + notif Telegram avec boutons/commandes :
  `approve <mintId>` / `cancel <mintId>`.
- `mint` ne part QUE si `approved`. Timeout (ex 2h sans réponse) → `cancelled` par défaut (capital-first).
- **Annulation à tout moment** : commande `cancel <mintId>` →
  - avant mint : passe `cancelled`, libère le budget.
  - mint déjà envoyé/confirmé : on ne peut pas annuler la TX, mais on bascule direct en logique de sortie (`sell`).
- Mode `AUTO_APPROVE` optionnel par mint à très haute conviction (désactivé par défaut).

### 4.6 `sell/` — sortie + derisk adaptatif
Déclenché dès qu'une `position` est `held`.
- **Ne JAMAIS dumper dans le creux post-mint** : à l'ouverture du secondary, le floor spike souvent puis s'effondre puis parfois reprend (ex Shellmates). Donc :
  - Fenêtre d'observation initiale (ex 15-60 min) avant toute vente.
  - Suivre le floor (Reservoir `collections/floor-ask` ou le watcher).
- **Plan de sortie** (`exit_plan`, défini par le skill) ex : `flip 60% sur la 1ère vague`, `hold 40% moonbag`, derisk = revendre le coût quand x2 atteint (mint "gratuit").
- **Trading parfois lock** jusqu'au reveal → si transferts bloqués, attendre (poll `transfersEnabled`/tenter un dry list).
- Lister via **Seaport** : `createOrder` (offer = NFT, consideration = ETH + fees) signé par le wallet détenteur, poster sur OpenSea (API) ou via Reservoir.
- **Closed-loop relisting** (repris de ton MM Diaso) : dès qu'une vente est détectée (watcher), reposter la prochaine unité au prix cible sans attendre le tick suivant.
- Chaque vente → `budget_ledger(proceeds)` + maj `positions(sold)` + notif PnL.

### 4.7 `notify/` + `control/`
- `notify` : messages Telegram lisibles (T-24h, mint live, mint done, vente, erreurs). Format texte clair, pas de JSON brut.
- `control` : CLI (`engine mint:add`, `engine approve <id>`, `engine cancel <id>`, `engine status`) **et** un petit serveur HTTP local (token bearer, comme le codex-gateway) pour que **Hermes/le skill** pilote l'engine (ajouter un mint, lire le statut). Réutilise le pattern du gateway `:8742`.

---

## 5. Config / env (`.env`, chmod 600)

```
ETH_RPC_URL=...            # Alchemy/Infura mainnet
BASE_RPC_URL=...           # Base
OPENSEA_API_KEY=...
RESERVOIR_API_KEY=...      # gratuit
ETHERSCAN_API_KEY=...      # résolution ABI custom
BASESCAN_API_KEY=...
WALLET_RAID_PK_FILE=/home/hermes/.hermes/secrets/wallet_raid_pk    # clé, chmod 600
WALLET_KRYSKO_PK_FILE=/home/hermes/.hermes/secrets/wallet_krysko_pk
CAP_WEI=250000000000000000 # 0.25 ETH global
MAX_GAS_ETH=0.01           # plafond gas par TX
AUTO_APPROVE=false
TELEGRAM_...               # réutiliser le canal Hermes
GATEWAY_URL=http://localhost:8742/v1/complete  # avis IA optionnel
```

Clés privées : déposées **à la main sur la VM** (nano + chmod 600), **jamais** en clair dans le repo / le chat / git.

---

## 6. Invariants de sécurité (règles dures, non négociables)

1. **Cap 0.25 ETH global jamais dépassé** (mint + gas), tous wallets confondus.
2. **Aucun `spend` sans `approved`** (sauf AUTO_APPROVE explicite par mint).
3. **Toujours `simulateContract` avant `writeContract`** ; revert simulé = abort.
4. **Annulable à tout instant** (avant mint : annule ; après : bascule en sortie).
5. **Plafond gas absolu** par TX (`MAX_GAS_ETH`).
6. **Idempotence** : jamais 2 mints pour le même `mint_id`.
7. **Pas de clé privée hors VM**. Lecture depuis fichier chmod 600.
8. **Capital-first** : en cas de doute / timeout / data manquante → ne pas minter.

---

## 7. Edge cases à gérer

- Mint **sold out** avant ton tour → simulate revert "exceeds max supply" → abort propre.
- **Gas war** / baseFee qui explose → si gas estimé > `MAX_GAS_ETH` → skip + notif.
- TX **revert** onchain (allowlist proof invalide, pas WL, mauvais prix) → log raison, pas de retry aveugle.
- **Mauvaise chain** (projet sur Base mais wallet pointé sur ETH) → check chainId.
- **Reveal / transfer lock** post-mint → ne pas paniquer, attendre, ne pas vendre dans le creux.
- **Prix de mint change** entre résolution et exécution → relire la config drop juste avant simulate.
- **Front-running / approval** : pour vendre via Seaport, approve l'opérateur (conduit) une seule fois par wallet/collection.
- **Nonce** géré par viem ; gérer les TX bloquées (replacement fee).

---

## 8. Interface avec l'existant (Hermes / skill / mémoire)

- **Source des ordres** : le skill `artbytes-mint-hunter` remplit `mints-watchlist.md` (déjà la structure : Statut, VERDICT, WL, Plan de sortie, Budget). L'engine lit ces blocs (ou mieux : le skill appelle l'API `control/` de l'engine pour `mint:add`).
- **Décision** reste côté skill (eval projet + hype-board). L'engine ne fait qu'exécuter un `decision != skip`.
- **Notifs** sur le même Telegram que les raids.
- **Avis IA** (optionnel) : avant un mint à grosse size, l'engine peut demander un go/no-go au codex gateway (`:8742`) en lui passant le contexte (hype-board, floor, supply).

---

## 9. Phases de build (du MVP au complet)

**Phase 1 — MVP mint manuel-assisté** (le plus utile vite)
- DB + budget ledger + config + wallets.
- `resolve` SeaDrop public.
- `mint executor` SeaDrop public avec simulate + approval Telegram + cap.
- Notif T-24h basique.
→ Tu peux minter un drop SeaDrop public en sécurité, avec ton approbation.

**Phase 2 — Sortie**
- `positions` + Reservoir floor + listing Seaport + derisk simple (flip X% / moonbag).
- Closed-loop relisting.

**Phase 3 — Autonomie & couverture**
- Allowlist SeaDrop (Merkle proof), contrats custom (resolver ABI), Base.
- Détection live fine + resserrage de polling.
- API `control/` pilotée par Hermes/skill.
- Avis IA go/no-go.

---

## 10. Questions à trancher (pour toi)

1. **viem** ok pour toi (vs ethers) ? (reco : viem)
2. Tu héberges l'engine **sur la VM** (à côté de Hermes/watcher) ? (reco : oui, accès clés + Telegram + gateway)
3. Listing : **Seaport direct** ou passer par **Reservoir execute** (plus simple) ?
4. `qty` par mint : on plafonne à combien d'unités par drop (au-delà du budget) ?
5. AUTO_APPROVE : on l'autorise un jour pour les très hautes convictions, ou approbation humaine **toujours** ?
