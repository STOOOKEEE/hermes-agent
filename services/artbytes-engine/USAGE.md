# artbytes-engine — Guide d'utilisation

Guide complet pour piloter le bot de mint. Pour la philosophie et les détails
techniques, voir [`SPEC.md`](./SPEC.md) ; pour l'aperçu rapide, [`README.md`](./README.md).

> **Principe** : *Hermes (ou toi) décide quoi minter, l'engine exécute.*
> Tout est plafonné par mint, tout passe par une approbation avant de dépenser,
> tout est annulable. En cas de doute, l'engine ne mint pas (**capital-first**).

---

## 1. Est-ce qu'on mint via le front OpenSea ? (réponse courte)

**Non.** Le bot ne clique pas sur le bouton "Mint" du site OpenSea. Il envoie la
transaction **directement on-chain** au contrat **SeaDrop** d'OpenSea
(`mintPublic`), depuis ton wallet. C'est plus rapide, scriptable, simulable, et
ça ne dépend pas du front.

**Et l'adresse du contrat ?** Tu n'as **pas** à la coller à la main. Tu donnes le
**slug** de la collection (le bout d'URL OpenSea), et l'engine récupère l'adresse
tout seul via l'API OpenSea, puis lit la config du drop on-chain. Tu peux aussi
fournir l'adresse directement avec `--contract` si tu l'as déjà. → voir §5.

---

## 2. Prérequis

- **Node 20+** et npm.
- Un **RPC** Ethereum (et Base si tu mintes sur Base) — Alchemy/Infura.
- Une **clé API OpenSea** (gratuite) — https://docs.opensea.io/reference/api-keys.
- Un **bot Telegram** (token via @BotFather) + ton **user id** (via @userinfobot).
- Les **clés privées** des wallets de mint, déposées sur la VM en chmod 600 (§4).

---

## 3. Installation & configuration

```bash
npm install
cp .env.example .env      # puis remplis-le (détail ci-dessous)
npm run build
npm test                  # vérifie que tout est sain (aucun ETH dépensé)
```

### Le fichier `.env`

| Variable | Rôle |
|---|---|
| `ETH_RPC_URL` | RPC mainnet (obligatoire) |
| `BASE_RPC_URL` | RPC Base (si tu mintes sur Base) |
| `OPENSEA_API_KEY` | résolution slug → contrat + floor (obligatoire) |
| `OPENSEA_API_KEY_FILE` | alternative recommandée : fichier chmod 600 contenant la clé OpenSea |
| `ETHERSCAN_API_KEY` / `BASESCAN_API_KEY` | résolution ABI custom (Phase 3) |
| `WALLET_RAID_PK_FILE` | chemin du fichier clé privée de `W_raid` |
| `WALLET_KRYSKO_PK_FILE` | chemin du fichier clé privée de `W_krysko` |
| `MAX_GAS_ETH` | plafond gas absolu par TX (défaut 0.01) — anti gas-war |
| `MAX_PRIORITY_GWEI` | pourboire validateur (défaut 1.5) |
| `BASEFEE_MULT` | `maxFee = baseFee × mult + priority` (défaut 2) |
| `AUTO_APPROVE` | `false` par défaut (approbation humaine obligatoire) |
| `APPROVAL_TIMEOUT_MS` | délai après quoi une demande non répondue est annulée (défaut 2h) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_USER_ID` | notifs + commandes |
| `DB_PATH` | base SQLite (défaut `./data/engine.db`) |
| `MONITOR_TICK_MS` | fréquence de la boucle de veille (défaut 60 s) |

> ⚠️ Il n'y a **pas** de cap budget global dans l'engine : **le budget est fixé
> par mint** (par l'IA / toi) dans l'ordre. Voir §8.

---

## 4. Les clés privées (sécurité)

Les clés vivent **uniquement sur la VM**, dans des fichiers en chmod 600 :

```bash
# sur la VM, jamais dans le repo, jamais dans le chat/git
mkdir -p ~/.hermes/secrets
nano ~/.hermes/secrets/wallet_raid_pk      # colle la clé (0x... 64 hex)
chmod 600 ~/.hermes/secrets/wallet_raid_pk
```

Puis pointe `WALLET_RAID_PK_FILE=/home/hermes/.hermes/secrets/wallet_raid_pk`
dans `.env`. L'engine lit le fichier, **warn si les perms ne sont pas 600**, et
ne logge/stocke jamais la clé. Deux wallets sont prévus : `W_raid` (défaut) et
`W_krysko` ; tu choisis lequel utiliser par mint avec `--wallet`.

---

## 5. Trouver un drop : slug ou adresse de contrat

### a) Le slug (méthode recommandée)

Le **slug** est le dernier segment de l'URL d'une collection OpenSea :

```
https://opensea.io/collection/glifs
                              ^^^^^   ← le slug = "glifs"
```

Tu le passes tel quel ; l'engine fait le reste :
1. interroge l'API OpenSea `/collections/glifs` → récupère l'adresse du contrat ;
2. lit `getPublicDrop(contract)` sur le SeaDrop on-chain → prix, début, fin,
   max par wallet.

```bash
node dist/control/cli.js mint:add --slug glifs --chain ethereum --budget 0.05
```

La sortie te montre l'adresse résolue, le prix, l'heure de début, etc.

### b) L'adresse de contrat (si tu l'as déjà / slug introuvable)

Parfois la collection n'a pas de page OpenSea propre, ou la résolution échoue.
Tu peux alors fournir l'adresse directement :

```bash
node dist/control/cli.js mint:add --contract 0xABCD…1234 --chain ethereum --budget 0.05
```

### c) Les deux ensemble (recommandé si tu les as)

Tu peux passer **slug + contrat**. C'est le meilleur cas :
- l'engine **résout via le contrat** (fiable, sans dépendre du lookup OpenSea) ;
- il **vérifie que le slug pointe vers la même adresse** — en cas de désaccord
  (faute de frappe, mauvaise adresse), il **refuse** au lieu de deviner
  (capital-first) ;
- le slug est conservé pour l'identité du mint et le floor (Phase 2).

```bash
node dist/control/cli.js mint:add --slug glifs --contract 0xABCD…1234 \
     --chain ethereum --budget 0.05
```

### c) Comment récupérer l'adresse du contrat à la main

- **Sur OpenSea** : page de la collection → onglet/section **Details** → "Contract
  Address" (lien cliquable vers Etherscan).
- **Sur Etherscan/Basescan** : l'adresse est dans l'URL `etherscan.io/address/0x…`
  ou `etherscan.io/token/0x…`.
- **Depuis le site officiel du projet** : souvent affichée près du bouton mint.

> Vérifie toujours que c'est bien un **drop SeaDrop public**. Si l'engine ne lit
> pas de drop public fiable, il marque le mint `manual` (→ §11, il t'enverra une
> notif "mint à la main" au lieu d'exécuter).

---

## 6. Cycle de vie d'un mint

```
watching ──(live + approuvé)──► minting ──(receipt ok)──► minted ──► (sold en P2)
   │  └──(live, pas encore approuvé)──► demande d'approbation Telegram
   ├──(timeout sans réponse / cancel)──► passed
   └──(simulate revert / gas trop haut / tx revert)──► failed
```

- **watching** : ordre ajouté, en attente du T-0 et/ou de ton approbation.
- **approved** : tu as approuvé ; l'engine mintera dès que le drop est live.
- **minting** : TX envoyée (verrou anti-double-mint).
- **minted** : confirmé, position(s) ouverte(s).
- **failed** : abandon propre (revert, gas, etc.) — budget relâché.
- **passed** : annulé ou expiré, ou mint manuel délégué à toi.

---

## 7. Ajouter un mint (`mint:add`)

```bash
node dist/control/cli.js mint:add \
  --slug <slug> | --contract 0x… \   # l'un des deux
  --chain ethereum \                  # ethereum | base (défaut ethereum)
  --decision mint-1 \                 # voir tableau ci-dessous
  --qty 1 \                           # nb d'unités visées
  --budget 0.05 \                     # budget ETH de CE mint (mint + gas)
  --wallet W_raid \                   # W_raid (défaut) | W_krysko
  --name "Glifs" \                    # libellé optionnel
  --stage "GTD" \                     # phase OpenSea signée optionnelle
  --fallback-public \                  # continue jusqu'à la phase publique si non éligible
  --priority-gwei 3 --basefee-mult 3 --max-gas-eth 0.02   # override gas (optionnel)
```

**`--decision`** détermine si l'engine agit :

| valeur | effet |
|---|---|
| `skip` / `watch` | l'engine **n'exécute pas** (suivi seulement) |
| `mint-1` | mint d'1 unité |
| `mint-conviction` | mint de `--qty` unités |
| `max` | conviction max ; **seule** valeur éligible à l'auto-approve (si activé) |

`--qty` est clampé au `maxTotalMintableByWallet` du drop, et le **budget reste le
garde-fou dur** : si `prix × qty + gas` dépasse `--budget`, l'engine refuse.

> Sans `--budget`, le mint n'a pas de budget → l'engine **refuse de dépenser**
> (capital-first). Le budget est défini par l'IA/toi, pas par le bot.

---

## 8. Le budget (défini par mint)

- Chaque ordre porte son propre `budget_wei` (`--budget` en ETH).
- Avant d'envoyer, l'engine **réserve** `prix × qty + plafond gas` contre ce
  budget ; si ça dépasse (même d'1 wei) → refus + notif, aucune TX.
- Après le mint, le ledger réconcilie : dépense réelle = valeur + gas réel, le
  reliquat réservé est relâché.
- `status` (CLI/Telegram) montre le ledger global (committed / spent / proceeds)
  et le budget de chaque mint. Les **proceeds de vente ne réaugmentent jamais**
  un budget (capital-first).

---

## 9. Le gas

Deux niveaux (voir aussi README §Gas) :
- **Global** via `.env` (`MAX_GAS_ETH`, `MAX_PRIORITY_GWEI`, `BASEFEE_MULT`).
- **Par mint** via `--priority-gwei` / `--basefee-mult` / `--max-gas-eth` : chaque
  champ override le global pour ce mint, les autres retombent sur l'env. Pratique
  pour pousser le pourboire sur un drop compétitif à T-0 sans toucher au reste.

Si le coût gas estimé dépasse le plafond (global ou du mint), l'engine **skip** et
te notifie — il ne rentre pas dans une gas-war aveugle.

---

## 10. Approuver / annuler

Quand un drop est live et que le mint est actionnable mais pas encore approuvé,
l'engine envoie sur Telegram une **demande d'approbation** avec deux boutons
(✅ Approve / ❌ Cancel) + le détail (contrat, qty, coût max).

- **Approuver** : bouton, ou `/approve <id>` (Telegram), ou
  `node dist/control/cli.js approve <id>` (CLI).
- **Annuler** : bouton, ou `/cancel <id>`, ou `cancel <id>` en CLI.
  - avant le mint → annulé + budget relâché ;
  - après l'envoi de la TX → on ne peut pas annuler la TX (la sortie/vente
    prendra le relais en Phase 2).
- **Timeout** : une demande sans réponse au bout de `APPROVAL_TIMEOUT_MS` est
  **annulée automatiquement** (capital-first).

---

## 11. Lancer l'engine

```bash
npm start        # ou: node dist/index.js
```

Au démarrage : ping Telegram (ledger + wallets configurés), puis la **boucle de
veille** tourne toutes les `MONITOR_TICK_MS`. À chaque tick, pour les mints
`watching`/`approved` :
1. envoie la notif **T-24h** (une fois) si le drop démarre dans ~24 h ;
2. expire les approbations périmées ;
3. si le drop est **live** :
   - `watching` + actionnable → **demande d'approbation** ;
   - `approved` → **exécute le mint** (idempotence → re-résolution du prix →
     budget → simulate → plafond gas → envoi → receipt → ledger + positions) ;
   - `manual` → notif "mint à la main MAINTENANT" (l'engine n'exécute pas).

---

## 12. Référence des commandes Telegram

| Commande | Effet |
|---|---|
| `/status` | ledger (committed/spent/proceeds) + compte des mints par état |
| `/mints` | liste des mints suivis (id, état, prix, contrat) |
| `/approve <id>` | approuve un mint |
| `/cancel <id>` | annule un mint (relâche le budget si pré-mint) |
| `/ping` | health check |
| `/help` | aide |

Seul ton `TELEGRAM_USER_ID` est autorisé (les autres sont ignorés).

---

## 13. Référence CLI

```bash
node dist/control/cli.js mint:add (--slug … | --contract 0x…) [flags]   # ajoute + résout un mint
node dist/control/cli.js approve <id>                                   # approuve
node dist/control/cli.js cancel  <id>                                   # annule + relâche budget
node dist/control/cli.js status                                         # ledger + mints (JSON)
```

Le CLI agit directement sur la base ; il peut tourner même quand l'engine est
lancé (même DB). Utile pour qu'Hermes pilote l'engine.

---

## 14. Déploiement Docker (VM)

```bash
docker compose up -d --build
docker logs -f artbytes-engine
docker exec artbytes-engine node dist/control/cli.js status
```

Le compose tourne en uid 1001 (hermes), monte `./data` pour la base SQLite et
`~/.hermes/secrets` en lecture seule pour les clés.

---

## 15. Invariants de sécurité (règles dures)

1. **Budget par mint défini par l'IA** ; refus si coût > budget, refus si pas de budget.
2. **Aucune dépense sans approbation** (`AUTO_APPROVE` opt-in par mint, off par défaut).
3. **Toujours `simulateContract` avant d'envoyer** ; revert simulé = abort.
4. **Annulable à tout instant**.
5. **Plafond gas absolu par TX** (global ou par mint).
6. **Idempotence** : jamais 2 mints pour le même ordre.
7. **Clés jamais hors VM** (fichiers chmod 600).
8. **Capital-first** : doute / timeout / data manquante → on ne mint pas.

---

## 16. Dépannage

| Symptôme | Cause / solution |
|---|---|
| `no budget_wei set` | tu as ajouté le mint sans `--budget`. Refixe via un nouvel ordre avec `--budget`. |
| `Per-mint budget exceeded` | `prix × qty + gas` > `--budget`. Augmente le budget ou baisse la qty. |
| `OpenSea collection not found` | slug erroné ; vérifie l'URL, ou passe `--contract 0x…`. |
| mint marqué `manual` | pas de drop SeaDrop public lisible ; mint à faire à la main (notif Telegram). |
| `gas too high` | gas estimé > plafond ; attends que la baseFee retombe, ou monte `--max-gas-eth`. |
| `exceeds max supply` (simulate revert) | sold out / ton tour passé → abort propre, budget relâché. |
| `wrong chain` | le wallet est sur une autre chain que le mint ; vérifie `--chain`. |
| pas de notif Telegram au boot | mauvais `TELEGRAM_USER_ID` (numérique, via @userinfobot). |

---

## 17. Limites actuelles (Phase 1) & suite

**Implémenté** : mint **SeaDrop public** de bout en bout (résolution, veille T-0,
budget par mint, approbation Telegram, simulate, plafond gas, positions, notifs).

**Pas encore** :
- **Phase 2 — sortie/vente** : suivi du floor, listing (OpenSea/Seaport ; Reservoir
  abandonné ; Blur en lecture ; Verse à étudier), derisk, closed-loop relisting.
- **Phase 3 — autonomie** : allowlist SeaDrop (Merkle), contrats custom, polling
  resserré à T-5min, API HTTP `control/` pour Hermes, avis IA go/no-go, Base
  de bout en bout.
