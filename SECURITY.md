# Politique de sécurité

Ce dépôt peut décrire les capacités d’un agent ayant accès à des comptes sociaux et à
des données financières. Les règles suivantes sont obligatoires.

## Ne jamais versionner

- tokens Discord, X/Twitter, providers IA ou APIs blockchain ;
- cookies de navigateur, sessions Telethon ou sessions Discord ;
- seed phrases, clés privées, keystores, hardware-wallet exports ;
- fichiers `~/.hermes/.env`, `auth.json`, bases de sessions ou mémoires runtime ;
- adresses de portefeuilles associées à une identité si leur publication n’est pas
  intentionnelle.

Les exemples doivent contenir des placeholders explicites. Une fuite de token impose
sa révocation immédiate, puis sa rotation sur le serveur.

## Limites opérationnelles

- `twitter` peut rechercher et préparer librement. Tout nouveau post demande une
  approbation Hermes sur le contenu exact. Une instruction utilisateur claire peut
  déclencher directement un reply, like ou follow unitaire ; aucune campagne ou
  interaction planifiée n'est autorisée.
- Agent Reach est limité à la lecture dans ce déploiement. Sa commande `twitter` est
  une façade à allowlist ; les sous-commandes mutantes de `twitter-cli` sont refusées.
- XActions n’est pas exposé comme MCP général. Le profil `twitter` charge uniquement
  le nouveau post approuvé et les outils unitaires reply, like et follow. Le nom du
  compte connecté est vérifié avant chaque écriture.
- Les cookies X vivent uniquement dans `~/.agent-reach/config.yaml` en mode `600` et
  sont transmis au sous-processus XActions par environnement, jamais comme argument de
  commande ni résultat d’outil.
- Les versions d’Agent Reach, twitter-cli et XActions restent figées. Toute mise à jour
  exige un nouvel audit et les tests du dépôt.
- `crypto` peut lire des données publiques et produire des alertes. Il ne doit jamais
  signer, transférer, swapper, trader, approuver un contrat ou modifier une allowance.
- Les salons Discord autorisés sont allowlistés. Le bot ne doit pas être ouvert à tous
  les utilisateurs.
- Chaque profil reçoit uniquement les secrets et outils nécessaires à sa mission.

## Risque résiduel X

Agent Reach, twitter-cli et XActions utilisent des interfaces web et des cookies de
session, pas l’API X officielle. X peut modifier ces interfaces sans préavis, limiter
une session ou suspendre un compte. Maintenir un faible volume, éviter les campagnes
automatisées et privilégier un compte dédié. Une approbation humaine réduit les
erreurs de contenu mais ne supprime pas le risque de plateforme.

## Signalement

En cas de secret détecté dans l’historique Git, ne pas se contenter de supprimer le
fichier dans un commit ultérieur : révoquer le secret, nettoyer l’historique et auditer
les journaux d’accès.
