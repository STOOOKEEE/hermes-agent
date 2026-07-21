#!/usr/bin/env python3
"""Génère un brouillon X avec le profil Twitter, sans outil de publication.

Ce script est conçu pour un job Hermes ``--no-agent``. Il lance un oneshot dans le
profil Twitter avec une surface volontairement limitée à la lecture X et au web, puis
écrit uniquement la proposition finale sur stdout pour livraison dans Discord.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROFILE_NAME = "twitter"
READ_ONLY_TOOLSETS = "agent_reach_reader,web"
TIMEOUT_SECONDS = 300
MAX_DELIVERY_LENGTH = 3_500


def _hermes_home() -> Path:
    configured = os.environ.get("HERMES_ROOT_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".hermes"


def _hermes_binary(root: Path) -> Path:
    candidates = (
        Path.home() / ".local" / "bin" / "hermes",
        root / "hermes-agent" / "venv" / "bin" / "hermes",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError("exécutable Hermes introuvable")


def _slot(now: datetime) -> str:
    return "matin" if now.hour < 14 else "fin de journée"


def build_prompt(now: datetime) -> str:
    """Construit une consigne autonome, bornée et sans mutation."""

    return f"""Tu exécutes la veille éditoriale X planifiée du {now:%d/%m/%Y} ({_slot(now)}).

Objectif : proposer UN brouillon original et pertinent pour le compte X connecté.
Cette tâche est strictement en lecture seule. Tu n'as pas l'outil de publication et tu
ne dois jamais tenter de publier, répondre, liker, suivre, retweeter ou modifier X.

Recherche, avec un volume faible :
1. appelle x_account_status ; si le compte n'est pas authentifié, arrête-toi et donne
   seulement une alerte exploitable ;
2. lis le profil et au maximum 8 posts récents du compte connecté ;
3. lis au maximum 10 posts du fil « for-you » ;
4. fais au plus UNE recherche X de 8 résultats si un angle précis se dégage ;
5. vérifie sur le web toute affirmation récente ou chiffrée avant de la reprendre.

Principes éditoriaux :
- privilégie la pertinence sémantique, l'utilité et une idée claire plutôt qu'une
  prétendue astuce d'algorithme ;
- place naturellement les mots-clés du sujet dès le début ;
- reste concis, conversationnel et spécifique ;
- évite les hashtags par défaut, les majuscules, le clickbait, les demandes de likes,
  les questions artificielles et le détournement d'un sujet tendance ;
- ne répète pas un post récent et ne fabrique aucun fait ;
- ajoute un lien source seulement s'il apporte une preuve utile ;
- rédige en français, sauf si le profil et l'audience observés indiquent clairement
  qu'un post en anglais est plus pertinent ;
- le texte exact du post doit tenir en 260 caractères, lien compris.

Réponds en moins de 1 400 caractères, exactement sous cette forme :

📝 **Brouillon X — {_slot(now)}**
> [texte exact proposé]

**Pourquoi cet angle :** [une phrase factuelle]
**Source :** [URL principale, ou « aucune affirmation externe »]
**Statut :** non publié — répondre « Publie exactement ce brouillon » pour lancer la
validation technique séparée.

Ne révèle jamais de cookie, token, chemin secret ou contenu brut de configuration.
"""


def _sanitize(text: str) -> str:
    """Évite qu'une erreur avalée par le modèle ressemble à un credential."""

    safe = re.sub(
        r"(?i)(auth_token|ct0)(\s*[=:]\s*)[^\s;,\"']+",
        r"\1\2[REDACTED]",
        text,
    ).strip()
    if len(safe) > MAX_DELIVERY_LENGTH:
        safe = safe[:MAX_DELIVERY_LENGTH].rstrip() + "…"
    return safe


def _delivery_only(text: str) -> str:
    """Retire un éventuel préambule du modèle avant la fiche de brouillon."""

    safe = _sanitize(text)
    marker = re.search(r"(?m)^📝\s+(?:\*\*)?Brouillon X", safe)
    if not marker:
        return safe
    delivery = safe[marker.start() :]
    status = re.search(r"(?m)^\*\*Statut\s*:\*\*.*$", delivery)
    if status:
        delivery = delivery[: status.end()]
    return delivery.strip()


def run(now: datetime | None = None) -> tuple[int, str]:
    current = now or datetime.now().astimezone()
    root = _hermes_home()
    profile_home = root / "profiles" / PROFILE_NAME
    if not profile_home.is_dir():
        return 0, "⚠️ Veille X non exécutée : profil Hermes twitter introuvable."

    try:
        binary = _hermes_binary(root)
    except FileNotFoundError as exc:
        return 0, f"⚠️ Veille X non exécutée : {exc}."

    env = os.environ.copy()
    env.update(
        {
            "HERMES_ROOT_HOME": str(root),
            "HERMES_HOME": str(profile_home),
            "HERMES_PROFILE": PROFILE_NAME,
        }
    )
    try:
        completed = subprocess.run(
            [
                str(binary),
                "--oneshot",
                build_prompt(current),
                "--toolsets",
                READ_ONLY_TOOLSETS,
            ],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 0, f"⚠️ Veille X non exécutée : {_sanitize(str(exc))}."

    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout or f"code {completed.returncode}"
        return 0, f"⚠️ Veille X en erreur : {_sanitize(detail)}"
    output = _delivery_only(completed.stdout)
    if not output:
        return 0, "⚠️ Veille X terminée sans brouillon."
    return 0, output


def main() -> int:
    status, output = run()
    print(output)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
