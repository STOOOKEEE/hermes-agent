# Diaso Guardian

Ce service lit les alertes Telegram déjà émises par les bots de production et publie
les décisions dans le salon Discord Diaso. Son API locale n'accepte que deux
opérations : `pause` et `panic`. Il n'existe aucune route `status` ou `resume`.

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

Toujours démarrer en simulation et exécuter les tests sans envoyer de `/status` aux
bots. L'armement ne doit passer à `true` qu'après ce smoke test. Un arrêt automatique est
sticky : seul l'opérateur humain peut envoyer `/resume` directement au bot concerné.

Chaque alerte de loan match contenant une ligne structurée `RISK` est vérifiée sans
commande sortante : Hermes recalcule `LTV = loanEq / exit`, contrôle l'âge du prix et
applique les seuils déterministes. Les anciens messages sans snapshot restent visibles
comme audits incomplets, sans inventer de valorisation.
