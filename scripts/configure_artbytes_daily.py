#!/usr/bin/env python3
"""Déploie les briefs quotidiens ArtBytes dans le salon Discord dédié."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNOWFLAKE_RE = re.compile(r"^[0-9]{15,22}$")
TARGET_SCRIPT_NAME = "artbytes_daily_context.py"
SKILL = "artbytes-mint-hunter"


class AutomationError(RuntimeError):
    """La configuration ne peut pas être appliquée sans intervention."""


@dataclass(frozen=True)
class ManagedJob:
    name: str
    schedule: str
    prompt: str
    aliases: tuple[str, ...] = ()


MORNING_PROMPT = """Produis le brief ArtBytes du matin en français à partir du contexte injecté.

Objectif : mettre en avant les mints confirmés du jour, puis les vérifications WL que
doit faire Armand aujourd'hui. Vérifie sur les sources officielles disponibles toute
date, heure, chaîne, prix, quantité et URL de checker récente. Convertis les horaires
en Europe/Paris. Ne transforme jamais une rumeur en fait et n'invente aucun checker.

Réponds toujours, même s'il n'y a aucun mint confirmé, en moins de 2 000 caractères :

🌅 **ArtBytes — brief du matin — JJ/MM**
🔥 **Mints aujourd'hui**
- projet — heure Paris — chaîne — prix — quantité — statut de confirmation — lien
  (ou « Aucun mint confirmé aujourd'hui »)
✅ **WL à checker aujourd'hui**
- projet — pourquoi vérifier — checker officiel ou « lien à obtenir » — statut connu
🗓 **Prochaines 72 h**
- seulement les échéances crédibles
⚠️ **À surveiller**
- changement, risque ou information encore incertaine

Sépare explicitement `WL confirmée`, `non WL` et `statut à vérifier`. Ne propose
aucune transaction, aucun achat automatique et ne révèle aucune adresse ou donnée
secrète. Le système livre directement ta réponse dans #artbytes : n'appelle aucun
outil de messagerie."""


EVENING_PROMPT = """Produis le récapitulatif ArtBytes du soir en français à partir des
messages du jour présents dans le contexte injecté. Résume ce dont la communauté a
réellement parlé, pas seulement les alertes de mint. Distingue faits, opinions de
membres, rumeurs, secondary et mints futurs. Ne présente jamais une prédiction de
prix comme certaine.

Réponds toujours en moins de 2 200 caractères :

🌙 **ArtBytes — récap du JJ/MM**
💬 **Sujets principaux** — 3 à 6 puces
🎯 **Mints / WL** — nouveaux horaires, checkers, statuts ou « aucun changement »
📈 **Convictions et alertes** — indique qui a formulé l'avis lorsque c'est utile
🧾 **Secondary / marché** — uniquement les mouvements discutés aujourd'hui
✅ **À faire demain** — checklist courte et concrète

Ignore le bruit sans le nier, conserve les liens utiles, signale les informations
manquantes et ne révèle aucune donnée secrète. Le système livre directement la
réponse dans #artbytes : n'appelle aucun outil de messagerie."""


MANAGED_JOBS = (
    ManagedJob(
        "artbytes-daily-morning",
        "30 8 * * *",
        MORNING_PROMPT,
        aliases=("artbytes-twitter-daily",),
    ),
    ManagedJob("artbytes-daily-evening", "30 22 * * *", EVENING_PROMPT),
)


def _load_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutomationError(f"registre cron illisible : {path}") from exc
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise AutomationError(f"format de registre cron inattendu : {path}")
    return jobs


def _managed_index(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for spec in MANAGED_JOBS:
        names = (spec.name, *spec.aliases)
        matches = [job for job in jobs if str(job.get("name") or "") in names]
        if len(matches) > 1:
            raise AutomationError(f"plusieurs jobs correspondent à {spec.name}")
        if matches:
            if not str(matches[0].get("id") or "").strip():
                raise AutomationError(f"job géré sans identifiant : {spec.name}")
            result[spec.name] = matches[0]
    return result


def _run(command: list[str], *, env: dict[str, str]) -> None:
    completed = subprocess.run(command, text=True, env=env, check=False)
    if completed.returncode != 0:
        raise AutomationError(
            f"commande Hermes en échec ({completed.returncode}) : {' '.join(command[:3])}"
        )


def _install_script(source: Path, target: Path, backup_root: Path) -> None:
    if target.exists() and target.read_bytes() == source.read_bytes():
        target.chmod(0o755)
        return
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_root / f"{target.name}.{stamp}.bak")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    shutil.copy2(source, temporary)
    temporary.chmod(0o755)
    os.replace(temporary, target)


def configure(*, channel_id: str, hermes_home: Path, hermes_bin: Path, apply: bool) -> None:
    if not SNOWFLAKE_RE.fullmatch(channel_id):
        raise AutomationError("channel-id doit être un identifiant Discord numérique")
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "automations" / TARGET_SCRIPT_NAME
    if not source.is_file():
        raise AutomationError(f"collecteur introuvable : {source}")
    if apply and not (hermes_bin.is_file() and os.access(hermes_bin, os.X_OK)):
        raise AutomationError(f"exécutable Hermes introuvable : {hermes_bin}")

    existing = _managed_index(_load_jobs(hermes_home / "cron" / "jobs.json"))
    delivery = f"discord:{channel_id}"
    target = hermes_home / "scripts" / TARGET_SCRIPT_NAME

    print("Plan automatisation ArtBytes :")
    print(f"- collecteur Discord read-only sans modification de curseur : {target}")
    for spec in MANAGED_JOBS:
        action = "mettre à jour" if spec.name in existing else "créer"
        print(f"- {action} {spec.name} à {spec.schedule} → {delivery}")
    if not apply:
        print("Aucun fichier modifié. Relancer avec --apply après vérification.")
        return

    _install_script(source, target, hermes_home / "backups" / "artbytes-daily")
    env = os.environ.copy()
    env.update({"HERMES_ROOT_HOME": str(hermes_home), "HERMES_HOME": str(hermes_home)})
    base = [str(hermes_bin), "cron"]
    for spec in MANAGED_JOBS:
        current = existing.get(spec.name)
        if current:
            job_id = str(current["id"])
            command = [
                *base,
                "edit",
                job_id,
                "--schedule",
                spec.schedule,
                "--prompt",
                spec.prompt,
                "--name",
                spec.name,
                "--deliver",
                delivery,
                "--skill",
                SKILL,
                "--script",
                TARGET_SCRIPT_NAME,
                "--agent",
            ]
            _run(command, env=env)
            if not current.get("enabled", True):
                _run([*base, "resume", job_id], env=env)
        else:
            _run(
                [
                    *base,
                    "create",
                    spec.schedule,
                    spec.prompt,
                    "--name",
                    spec.name,
                    "--deliver",
                    delivery,
                    "--skill",
                    SKILL,
                    "--script",
                    TARGET_SCRIPT_NAME,
                ],
                env=env,
            )
    print("Automatisation ArtBytes installée.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--hermes-home", type=Path, default=Path("~/.hermes").expanduser())
    parser.add_argument(
        "--hermes-bin", type=Path, default=Path("~/.local/bin/hermes").expanduser()
    )
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        configure(
            channel_id=args.channel_id,
            hermes_home=args.hermes_home.expanduser().resolve(),
            hermes_bin=args.hermes_bin.expanduser().resolve(),
            apply=args.apply,
        )
    except (AutomationError, OSError) as exc:
        print(f"Configuration refusée : {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
