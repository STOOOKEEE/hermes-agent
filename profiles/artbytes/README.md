# Profil Artbytes

Ce profil isole la veille Artbytes et la préparation des mints dans un salon Discord
dédié. Il utilise le skill serveur `artbytes-mint-hunter`, qui reste read-only.

L'engine `/home/hermes/dev/NFT/artbytes-engine` est une composante distincte. Il ne
doit être exposé à Hermes qu'au travers d'outils structurés avec validation stricte,
budget par mint, simulation on-chain et approbation humaine. Aucun secret de wallet
ne doit être copié dans ce dépôt ou dans Discord.

## Briefs quotidiens

Deux tâches livrent un point structuré dans le salon Artbytes, heure Europe/Paris :

- `artbytes-daily-morning` à 08 h 30 met en avant les mints confirmés du jour et les
  statuts WL à vérifier ;
- `artbytes-daily-evening` à 22 h 30 résume les sujets discutés, les changements de
  conviction, le secondary et la checklist du lendemain.

Le collecteur `artbytes_daily_context.py` relit une fenêtre récente de Discord sans
modifier le curseur du scanner incrémental `discord_scan.py`. Les deux jobs chargent
le skill `artbytes-mint-hunter`, distinguent les faits des rumeurs et ne déclenchent
aucune action on-chain.

Afficher le plan puis l'appliquer :

```bash
python3 scripts/configure_artbytes_daily.py --channel-id 1529427745136971888
python3 scripts/configure_artbytes_daily.py --channel-id 1529427745136971888 --apply
```
