# Profil `obsidian`

Ce profil donne à Hermes une surface étroite pour travailler dans un vault Obsidian
privé sans lui ouvrir un terminal général.

## Variables du profil

Copier `profile.env.example` dans le `.env` du profil déployé et remplacer le chemin :

```bash
OBSIDIAN_VAULT_PATH=/home/hermes/vaults/obsidian
```

Le chemin doit être absolu et désigner la racine du dépôt Git du vault. Les sauvegardes
des fichiers remplacés sont écrites hors du dépôt dans le dossier de sauvegarde du
profil Hermes.

## Accès au dépôt privé

Utiliser une clé SSH de déploiement dédiée à ce seul dépôt GitHub, avec accès en
écriture. Ne jamais placer de Personal Access Token dans Discord, le vault ou un URL
Git. Le remote `origin` doit rester au format SSH, par exemple :

```text
git@github.com:OWNER/VAULT-PRIVE.git
```

La synchronisation n'est jamais implicite : `obsidian_git_sync` crée un commit, fait
un pull avec rebase puis pousse uniquement après approbation. En cas de conflit, le
plugin abandonne le rebase et conserve le commit local pour une résolution manuelle.

## Outils exposés

- `obsidian_list_files`
- `obsidian_search_notes`
- `obsidian_read_file`
- `obsidian_write_file`
- `obsidian_append_note`
- `obsidian_git_status`
- `obsidian_git_sync` (avec approbation)
