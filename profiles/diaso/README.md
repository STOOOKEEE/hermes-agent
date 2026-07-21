# Profil `diaso`

Surface Discord réduite pour surveiller et arrêter les bots Diaso sans exposer leur
infrastructure ou leurs credentials au modèle.

Le plugin parle uniquement au socket Unix `diaso-guardian.sock` du service local. Il
ne connaît ni token Telegram, ni clé de wallet, ni commande arbitraire. Outils exposés :

- `diaso_pause`
- `diaso_panic`

La reprise reste volontairement absente. Elle doit être réalisée manuellement après
diagnostic dans le canal opérateur du bot.
