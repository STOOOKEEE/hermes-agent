# Hermes sur Discord

Ce dépôt est la couche de configuration personnelle de l’agent Hermes installé sur
le serveur. Il ne contient pas le moteur
[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) : le
moteur reste installé et mis à jour séparément dans `~/.hermes/hermes-agent`.

L’objectif est simple : **un salon Discord = une mission et une mémoire isolées**.

Configuration initiale prévue :

| Salon | Profil Hermes | Mission |
|---|---|---|
| `#général` | `default` | Conversation générale avec le profil Hermes principal |
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
- Le profil `twitter` demande une validation explicite avant tout nouveau post. Les
  replies, likes et follows unitaires demandés dans `#x` n'ajoutent pas de seconde
  validation ; les suppressions et messages privés restent indisponibles.
- Le profil `crypto` est strictement en lecture seule et ne signe aucune transaction.

Voir aussi [SECURITY.md](SECURITY.md).

## Intégration X : Agent Reach + XActions

Les deux projets ont des rôles distincts :

- [`Panniantong/Agent-Reach`](https://github.com/Panniantong/Agent-Reach) route
  les recherches et lectures vers `twitter-cli` ;
- [`nirholas/XActions`](https://github.com/nirholas/XActions) sert aux écritures
  unitaires explicitement exposées par le plugin Hermes versionné ici.

Le MCP complet de XActions n’est volontairement pas exposé : sa surface comprend de
nombreuses mutations qui ne sont pas nécessaires. Le plugin `xactions-publisher`
enregistre quatre actions unitaires et vérifie le compte connecté avant chacune :

- `xactions_post_tweet` conserve l'approbation Hermes sur le texte exact ;
- `xactions_reply_tweet`, `xactions_like_tweet` et `xactions_follow_user` s'exécutent
  directement après une instruction claire de l'utilisateur dans `#x`, sans seconde
  fenêtre d'approbation.

Un refus, une expiration ou une erreur du portail d’approbation bloque toujours un
nouveau post. Les interactions directes n'acceptent ni liste ni opération en masse et
ne sont pas accessibles aux deux tâches éditoriales planifiées.

Le plugin `agent-reach-reader` exécute les lectures X sur l’hôte et expose au profil
Twitter des outils structurés de statut, recherche, profil, posts, tweet et fil
d’accueil. Le conteneur Docker de raisonnement ne reçoit ni les cookies ni un montage
du dossier Agent Reach. Chaque lecture vérifie que la session correspond au compte X
attendu avant d’interroger les données.
En mode multiplexé, les plugins sont chargés par le processus gateway principal puis
filtrés à chaque tour avec le profil actif et son coffre de secrets contextuel. Ils
restent donc indisponibles dans le profil crypto.

### Veille éditoriale quotidienne

L'automatisation proposée génère un brouillon à 09 h 15 et un autre à 17 h 30
(heure du serveur). Chaque tâche lance le profil Twitter avec uniquement les toolsets
`agent_reach_reader` et `web` : `xactions_publisher` n'est pas chargé et une tâche
planifiée ne peut donc pas publier. Le résultat est livré dans `#x`, avec un texte
exact à valider séparément.

Le générateur privilégie les signaux durables documentés par X : pertinence du sujet,
langage clair et naturel, originalité, utilité et absence de spam. Il évite les
hashtags par défaut, l'engagement artificiel, les doublons et le détournement de sujets
tendance. Il ne prétend pas garantir une portée ou « hacker » le classement.

Références officielles utilisées pour cette politique :

- [code public du système de recommandation X](https://github.com/xai-org/x-algorithm) ;
- [bonnes pratiques organiques X](https://business.x.com/en/basics/organic-best-practices) ;
- [règles X relatives à l'automatisation](https://help.x.com/en/rules-and-policies/x-automation?lang=browser).

Afficher le plan puis l'appliquer sur le serveur :

```bash
python3 scripts/configure_x_automation.py \
  --channel-id IDENTIFIANT_DU_SALON_X

python3 scripts/configure_x_automation.py \
  --channel-id IDENTIFIANT_DU_SALON_X \
  --apply
```

Le script crée ou met à jour uniquement `x-editorial-morning` et
`x-editorial-evening`. Les autres tâches cron restent intactes. Le générateur déployé
est sauvegardé avant remplacement et aucune donnée de session X n'est lue par
l'installateur.

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
Les sauvegardes de plugins sont conservées dans `backups/plugins/`, hors du dossier
exécutable `plugins/`, afin que Hermes ne recharge jamais une ancienne version.
Les variables `DISCORD_*` et `TELEGRAM_*` sont retirées des `.env` clonés : le gateway
principal reste l’unique propriétaire des bots et route ensuite chaque salon vers son
profil.

La liste optionnelle `toolsets` de chaque salon remplace, pour Discord seulement, la
surface d’outils héritée par son profil. Le profil Twitter exclut ainsi le terminal et
`execute_code` : les noms `x_...` sont nécessairement appelés comme outils structurés,
et les intégrations globales MCP sont désactivées dans ce salon avec `no_mcp`.
Le plugin réapplique également la route multiplexée avant les commandes slash : `/new`
et `/reset` réinitialisent donc la session du salon (`agent:twitter` ou `agent:crypto`),
pas la session générale `agent:main`.

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

Le nom de profil spécial `default` autorise un salon sans créer de route multiplexée :
Hermes utilise alors son home principal et la session `agent:main`. Il convient au
salon général, tandis que les missions isolées gardent un profil dédié.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/install_social_tools.py
python3 scripts/configure_discord.py \
  --manifest config/discord-channels.example.yaml \
  --check-template
```
