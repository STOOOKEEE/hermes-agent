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

- `twitter` peut rechercher et préparer librement, mais toute publication,
  suppression, réponse ou DM demande une approbation humaine sur le contenu exact.
- `crypto` peut lire des données publiques et produire des alertes. Il ne doit jamais
  signer, transférer, swapper, trader, approuver un contrat ou modifier une allowance.
- Les salons Discord autorisés sont allowlistés. Le bot ne doit pas être ouvert à tous
  les utilisateurs.
- Chaque profil reçoit uniquement les secrets et outils nécessaires à sa mission.

## Signalement

En cas de secret détecté dans l’historique Git, ne pas se contenter de supprimer le
fichier dans un commit ultérieur : révoquer le secret, nettoyer l’historique et auditer
les journaux d’accès.
