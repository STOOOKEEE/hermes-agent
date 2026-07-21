# Profil `twitter`

`SOUL.md` et `plugins/xactions-publisher/` sont déployés dans le profil
`~/.hermes/profiles/twitter/`. Le script ajoute le plugin à
`plugins.enabled` dans la configuration propre à ce profil.

Les cookies X/Twitter doivent être configurés directement sur le serveur avec
`agent-reach configure twitter-cookies`, puis protégés avec `chmod 600
~/.agent-reach/config.yaml`. Ils ne doivent pas être ajoutés à ce dossier.

Ajouter `XACTIONS_EXPECTED_USERNAME=nom_sans_arobase` dans le `.env` privé du profil.
Le plugin refuse une publication si le compte réellement associé aux cookies ne
correspond pas.
