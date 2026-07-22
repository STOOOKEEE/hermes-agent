# Profil Artbytes

Ce profil isole la veille Artbytes et la préparation des mints dans un salon Discord
dédié. Il utilise le skill serveur `artbytes-mint-hunter`, qui reste read-only.

L'engine `/home/hermes/dev/NFT/artbytes-engine` est une composante distincte. Il ne
doit être exposé à Hermes qu'au travers d'outils structurés avec validation stricte,
budget par mint, simulation on-chain et approbation humaine. Aucun secret de wallet
ne doit être copié dans ce dépôt ou dans Discord.
