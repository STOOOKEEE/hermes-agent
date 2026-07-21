# Hermes sur Discord

Ce dépôt est la couche de configuration personnelle de l’agent Hermes installé sur
le serveur. Il ne contient pas le moteur
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) : le
moteur reste installé et mis à jour séparément dans `~/.hermes/hermes-agent`.

L’objectif est simple : **un salon Discord = une mission et une mémoire isolées**.

Configuration initiale prévue :

| Salon | Profil Hermes | Mission |
|---|---|---|
| `#twitter` | `twitter` | Veille avec Agent Reach, rédaction et publication contrôlée via XActions |
| `#crypto` | `crypto` | Surveillance en lecture seule de portefeuilles crypto |

Hermes sait déjà faire ce routage nativement avec `gateway.multiplex_profiles` et
`gateway.profile_routes`. Ce dépôt ne maintient donc aucun fork du cœur.

## Sécurité

- Le token Discord et les identifiants autorisés restent dans `~/.hermes/.env`.
- Les cookies X/Twitter restent dans `~/.agent-reach/config.yaml` en mode `600`.
- Les adresses publiques à surveiller restent dans
  `~/.hermes/profiles/crypto/WALLETS.md`.
- Une seed phrase, une clé privée ou un fichier de wallet ne doit jamais être donné à
  l’agent ni ajouté à ce dépôt.
- Le profil `twitter` demande une validation explicite avant de publier, supprimer ou
  envoyer un message privé.
- Le profil `crypto` est strictement en lecture seule et ne signe aucune transaction.

Voir aussi [SECURITY.md](SECURITY.md).

## Intégration X : Agent Reach + XActions

Les deux projets ont des rôles distincts :

- [`Panniantong/Agent-Reach`](https://github.com/Panniantong/Agent-Reach) route
  les recherches et lectures vers `twitter-cli` ;
- [`nirholas/XActions`](https://github.com/nirholas/XActions) sert uniquement à
  publier un nouveau post texte grâce au plugin Hermes versionné ici.

Le MCP complet de XActions n’est volontairement pas exposé : sa surface comprend de
nombreuses mutations qui ne sont pas nécessaires. Le plugin
`xactions-publisher` enregistre seulement `xactions_post_tweet`, vérifie le compte
connecté avant l’envoi et utilise le mécanisme d’approbation natif de Hermes. Le texte
exact apparaît dans la demande d’approbation ; un refus, une expiration ou une erreur
du portail d’approbation bloque l’action.

Le plugin `agent-reach-reader` exécute les lectures X sur l’hôte et expose au profil
Twitter des outils structurés de statut, recherche, profil, posts, tweet et fil
d’accueil. Le conteneur Docker de raisonnement ne reçoit ni les cookies ni un montage
du dossier Agent Reach. Chaque lecture vérifie que la session correspond au compte X
attendu avant d’interroger les données.

Les révisions auditées sont figées dans
[`integrations/versions.yaml`](integrations/versions.yaml). XActions est exécuté
directement depuis ses modules HTTP nécessaires : aucun `npm install` ni script npm
du dépôt tiers n’est exécuté.

### Installer les dépendances sociales

Le script annonce son plan sans rien modifier :

```bash
python3 scripts/install_social_tools.py
python3 scripts/install_social_tools.py --apply
```

Il crée un environnement Python privé dans
`~/.local/share/hermes-social/agent-reach-venv`, installe Agent Reach et
`twitter-cli` aux versions figées, puis place XActions au commit audité. La commande
publique `twitter` est une façade en lecture seule ; les commandes `post`, `reply`,
`like`, `follow`, etc. sont refusées.

Le profil et son plugin peuvent être déployés avant même de connaître les identifiants
Discord, sans toucher au gateway :

```bash
python3 scripts/configure_discord.py \
  --manifest config/discord-channels.example.yaml \
  --hermes-home ~/.hermes \
  --profiles-only

python3 scripts/configure_discord.py \
  --manifest config/discord-channels.example.yaml \
  --hermes-home ~/.hermes \
  --profiles-only \
  --apply
```

### Configurer X sans transmettre de cookie au bot

Se connecter en SSH au serveur, puis saisir les cookies directement dans ce terminal
(jamais dans Discord, une issue ou un commit) :

```bash
agent-reach configure twitter-cookies AUTH_TOKEN CT0
chmod 600 ~/.agent-reach/config.yaml
agent-reach doctor --json
twitter status
```

Définir aussi le compte attendu dans le fichier privé du profil :

```dotenv
# ~/.hermes/profiles/twitter/.env
XACTIONS_EXPECTED_USERNAME=nom_sans_arobase
```

Cette valeur n’est pas secrète, mais elle est obligatoire : XActions interroge X avant
chaque publication et refuse si les cookies correspondent à un autre compte.

Sur un VPS, rester très conservateur sur la fréquence. Les appels automatisés par
cookie et les IP de datacenter peuvent déclencher les protections de X. Un compte
secondaire dédié et, si nécessaire, un proxy résidentiel configuré côté serveur sont
préférables au compte personnel principal.

## 1. Préparer Discord

Dans le portail développeur Discord :

1. créer une application et son bot ;
2. activer **Server Members Intent** et **Message Content Intent** ;
3. inviter le bot avec les scopes `bot` et `applications.commands` ;
4. lui donner au minimum les droits de voir les salons, lire l’historique, envoyer des
   messages, joindre des fichiers et répondre dans les threads ;
5. activer le mode développeur Discord puis copier l’identifiant du serveur, des deux
   salons et de l’utilisateur autorisé.

Le token ne doit pas être envoyé dans Discord ou commité. Sur le serveur :

```bash
cp discord.env.example ~/.hermes/.env.discord-example
chmod 600 ~/.hermes/.env
```

Ajouter ensuite ces variables à `~/.hermes/.env` :

```dotenv
DISCORD_BOT_TOKEN=valeur_stockee_uniquement_sur_le_serveur
DISCORD_ALLOWED_USERS=identifiant_discord_du_proprietaire
DISCORD_HOME_CHANNEL=identifiant_du_salon_de_notifications
```

## 2. Décrire les salons

```bash
cp config/discord-channels.example.yaml config/discord-channels.yaml
```

Remplacer les trois valeurs `REPLACE_WITH_...` dans le fichier copié. Le fichier réel
est ignoré par Git afin de ne pas publier la topologie personnelle du serveur Discord.

## 3. Valider et appliquer

Le script est non destructif par défaut. Il préserve les routes qui ne sont pas gérées
par ce dépôt et annonce seulement les changements prévus :

```bash
python3 -m pip install -r requirements.txt
python3 scripts/configure_discord.py \
  --manifest config/discord-channels.yaml \
  --hermes-home ~/.hermes \
  --check

python3 scripts/configure_discord.py \
  --manifest config/discord-channels.yaml \
  --hermes-home ~/.hermes
```

Pour appliquer après vérification :

```bash
python3 scripts/configure_discord.py \
  --manifest config/discord-channels.yaml \
  --hermes-home ~/.hermes \
  --apply \
  --restart
```

Lors du premier passage, le script crée les profils avec `hermes profile create
--clone-from default`. Cela conserve le provider, le modèle et les skills actuels sans
mettre leurs secrets dans Git. Avant chaque écriture, une sauvegarde horodatée est
créée à côté du fichier concerné.

Le même passage déploie les plugins présents dans `profiles/<profil>/plugins/` et les
ajoute à l’allowlist `plugins.enabled` du profil concerné. Ainsi,
`xactions-publisher` n’existe que dans le profil `twitter`, jamais dans `crypto`.
Les variables `DISCORD_*` et `TELEGRAM_*` sont retirées des `.env` clonés : le gateway
principal reste l’unique propriétaire des bots et route ensuite chaque salon vers son
profil.

Le redémarrage refuse de s’exécuter si `DISCORD_BOT_TOKEN` ou
`DISCORD_ALLOWED_USERS` est absent de l’environnement et de `~/.hermes/.env`.

## 4. Couper Telegram après validation

Garder Telegram actif pendant le premier test Discord permet un retour arrière simple.
Quand les deux salons Discord répondent avec le bon profil :

1. sauvegarder `~/.hermes/.env` avec des permissions privées ;
2. retirer `TELEGRAM_BOT_TOKEN` de `~/.hermes/.env` ;
3. redémarrer le gateway puis vérifier `hermes gateway status` ;
4. révoquer le token auprès de BotFather si Telegram ne doit plus jamais être utilisé.

Cette coupure est volontairement manuelle : le script ne supprime jamais un credential.

## 5. Ajouter un nouveau salon

1. ajouter son entrée dans `config/discord-channels.yaml` ;
2. créer `profiles/<profil>/SOUL.md` avec une mission étroite ;
3. exécuter `--check`, puis `--apply --restart` ;
4. limiter les outils et credentials du nouveau profil au strict nécessaire.

Les threads héritent automatiquement du profil de leur salon parent. Les salons
configurés avec `respond_without_mention: true` répondent sans `@Hermes` et restent en
mode conversation directe.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/install_social_tools.py
python3 scripts/configure_discord.py \
  --manifest config/discord-channels.example.yaml \
  --check-template
```
