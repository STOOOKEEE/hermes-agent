# Diaso Guardian

Ce service lit les alertes Telegram déjà émises par les bots de production et publie
les décisions dans le salon Discord Diaso. Son API locale n'accepte que trois
opérations : `status`, `pause` et `panic`. Il n'existe aucune route `resume`.

## Variables privées

À conserver dans `/home/hermes/telethon-listener/.env` en mode `0600` :

```text
DIASO_LENDING_GROUP_ID=-4917590165
DIASO_LENDING_BOT_USERNAME=nft_lender_prod_bot
DIASO_MM_GROUP_ID=-5500138739
DIASO_MM_BOT_USERNAME=NFT_Market_Making_bot
DIASO_DISCORD_CHANNEL_ID=1529096343782559814
DIASO_GUARDIAN_ARMED=false
```

Le token Discord reste dans `~/.hermes/.env`. La session Telethon existante reste dans
`~/telethon-listener/hermes_session.session`.

Toujours démarrer en simulation, exécuter les tests puis vérifier les deux `/status`.
L'armement ne doit passer à `true` qu'après ce smoke test. Un arrêt automatique est
sticky : seul l'opérateur humain peut envoyer `/resume` directement au bot concerné.
