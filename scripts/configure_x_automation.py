#!/usr/bin/env python3
"""Déploie deux propositions X quotidiennes dans un salon Discord.

Le mode par défaut affiche le plan. ``--apply`` copie le générateur sous
``~/.hermes/scripts`` et crée ou met à jour uniquement les deux jobs nommés ici.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNOWFLAKE_RE = re.compile(r"^[0-9]{15,22}$")
TARGET_SCRIPT_NAME = "x_editorial_brief.py"


class AutomationError(RuntimeError):
    """La configuration ne peut pas être appliquée sans intervention."""


class ManagedJob:
    def __init__(self, name: str, schedule: str) -> None:
        self.name = name
        self.schedule = schedule


MANAGED_JOBS = (
    ManagedJob("x-editorial-morning", "15 9 * * *"),
    ManagedJob("x-editorial-evening", "30 17 * * *"),
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
    names = {spec.name for spec in MANAGED_JOBS}
    for job in jobs:
        name = str(job.get("name") or "")
        if name not in names:
            continue
        if name in result:
            raise AutomationError(f"plusieurs jobs portent le nom géré {name}")
        if not str(job.get("id") or "").strip():
            raise AutomationError(f"job géré sans identifiant : {name}")
        result[name] = job
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


def configure(
    *,
    channel_id: str,
    hermes_home: Path,
    hermes_bin: Path,
    apply: bool,
) -> None:
    if not SNOWFLAKE_RE.fullmatch(channel_id):
        raise AutomationError("channel-id doit être un identifiant Discord numérique")

    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "automations" / TARGET_SCRIPT_NAME
    if not source.is_file():
        raise AutomationError(f"générateur introuvable : {source}")
    if apply and not (hermes_bin.is_file() and os.access(hermes_bin, os.X_OK)):
        raise AutomationError(f"exécutable Hermes introuvable : {hermes_bin}")

    jobs_path = hermes_home / "cron" / "jobs.json"
    existing = _managed_index(_load_jobs(jobs_path))
    delivery = f"discord:{channel_id}"
    target = hermes_home / "scripts" / TARGET_SCRIPT_NAME

    print("Plan automatisation X :")
    print(f"- générateur lecture seule : {target}")
    for spec in MANAGED_JOBS:
        action = "mettre à jour" if spec.name in existing else "créer"
        print(f"- {action} {spec.name} à {spec.schedule} → {delivery}")
    print("- aucun outil de publication chargé ; un brouillon par exécution")
    if not apply:
        print("Aucun fichier modifié. Relancer avec --apply après vérification.")
        return

    _install_script(
        source,
        target,
        hermes_home / "backups" / "x-automation",
    )
    env = os.environ.copy()
    env.update(
        {
            "HERMES_ROOT_HOME": str(hermes_home),
            "HERMES_HOME": str(hermes_home),
        }
    )
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
                "--name",
                spec.name,
                "--deliver",
                delivery,
                "--script",
                TARGET_SCRIPT_NAME,
                "--no-agent",
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
                    "--name",
                    spec.name,
                    "--deliver",
                    delivery,
                    "--script",
                    TARGET_SCRIPT_NAME,
                    "--no-agent",
                ],
                env=env,
            )
    print("Automatisation X installée.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-id", required=True, help="identifiant du salon Discord #x")
    parser.add_argument(
        "--hermes-home",
        type=Path,
        default=Path("~/.hermes").expanduser(),
        help="racine Hermes principale",
    )
    parser.add_argument(
        "--hermes-bin",
        type=Path,
        default=Path("~/.local/bin/hermes").expanduser(),
        help="exécutable Hermes",
    )
    parser.add_argument("--apply", action="store_true", help="appliquer le plan")
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
