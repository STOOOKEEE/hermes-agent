#!/usr/bin/env python3
"""Déploie le service Diaso Guardian sans afficher les credentials existants."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


MANAGED = {
    "DIASO_LENDING_GROUP_ID": "-4917590165",
    "DIASO_LENDING_BOT_USERNAME": "nft_lender_prod_bot",
    "DIASO_MM_GROUP_ID": "-5500138739",
    "DIASO_MM_BOT_USERNAME": "NFT_Market_Making_bot",
    "DIASO_DISCORD_CHANNEL_ID": "1529096343782559814",
}
REQUIRED_EXISTING = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")


class GuardianConfigurationError(RuntimeError):
    pass


def _keys_with_values(text: str) -> set[str]:
    found: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip().strip("\"'"):
            found.add(key.strip())
    return found


def render_env(existing: str, *, armed: bool) -> str:
    values = {**MANAGED, "DIASO_GUARDIAN_ARMED": "true" if armed else "false"}
    result: list[str] = []
    replaced: set[str] = set()
    for line in existing.splitlines():
        if line.strip() == "# Diaso Guardian (géré par hermes-agent)":
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip())
        key = match.group(1) if match else ""
        if key in values:
            if key not in replaced:
                result.append(f"{key}={values[key]}")
                replaced.add(key)
            continue
        result.append(line)
    if result and result[-1].strip():
        result.append("")
    result.append("# Diaso Guardian (géré par hermes-agent)")
    for key, value in values.items():
        if key not in replaced:
            result.append(f"{key}={value}")
    return "\n".join(result).rstrip() + "\n"


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.diaso.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _has_discord_token(hermes_env: Path) -> bool:
    if os.getenv("DISCORD_BOT_TOKEN", "").strip():
        return True
    if not hermes_env.is_file():
        return False
    return "DISCORD_BOT_TOKEN" in _keys_with_values(
        hermes_env.read_text(encoding="utf-8")
    )


def configure(
    *,
    env_path: Path,
    hermes_env: Path,
    unit_source: Path,
    unit_target: Path,
    armed: bool,
    apply: bool,
    activate: bool,
) -> None:
    if not env_path.is_file():
        raise GuardianConfigurationError(f"fichier absent: {env_path}")
    existing = env_path.read_text(encoding="utf-8")
    missing = [key for key in REQUIRED_EXISTING if key not in _keys_with_values(existing)]
    if missing:
        raise GuardianConfigurationError(
            "credentials Telegram existants manquants: " + ", ".join(missing)
        )
    if not _has_discord_token(hermes_env):
        raise GuardianConfigurationError("DISCORD_BOT_TOKEN absent du home Hermes")
    session = env_path.parent / "hermes_session.session"
    if not session.is_file():
        raise GuardianConfigurationError(f"session Telethon absente: {session}")
    if session.stat().st_mode & 0o077:
        raise GuardianConfigurationError(f"permissions trop ouvertes sur {session}")
    if not unit_source.is_file():
        raise GuardianConfigurationError(f"unit systemd absente: {unit_source}")

    rendered = render_env(existing, armed=armed)
    if not apply:
        print(
            f"Configuration valide · mode {'ARMÉ' if armed else 'SIMULATION'} · "
            f"{len(MANAGED)} routes vérifiées"
        )
        return

    stamp = _timestamp()
    backup = env_path.with_name(f"{env_path.name}.bak.diaso-{stamp}")
    shutil.copy2(env_path, backup)
    backup.chmod(0o600)
    _atomic_write(env_path, rendered, 0o600)

    unit_target.parent.mkdir(parents=True, exist_ok=True)
    if unit_target.exists():
        unit_backup = unit_target.with_name(f"{unit_target.name}.bak.diaso-{stamp}")
        shutil.copy2(unit_target, unit_backup)
    shutil.copy2(unit_source, unit_target)
    unit_target.chmod(0o644)
    print(f"Diaso Guardian configuré · mode {'ARMÉ' if armed else 'SIMULATION'}")

    if activate:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", "lending-ear.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "diaso-guardian.service"],
            check=True,
        )
        print("Service diaso-guardian activé ; ancien lending-ear désactivé")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env", type=Path, default=Path.home() / "telethon-listener" / ".env"
    )
    parser.add_argument(
        "--hermes-env", type=Path, default=Path.home() / ".hermes" / ".env"
    )
    parser.add_argument(
        "--unit-target",
        type=Path,
        default=Path.home() / ".config" / "systemd" / "user" / "diaso-guardian.service",
    )
    parser.add_argument("--arm", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if args.activate and not args.apply:
        parser.error("--activate exige --apply")

    repo_root = Path(__file__).resolve().parents[1]
    try:
        configure(
            env_path=args.env.expanduser().resolve(),
            hermes_env=args.hermes_env.expanduser().resolve(),
            unit_source=repo_root
            / "integrations"
            / "diaso_guardian"
            / "diaso-guardian.service",
            unit_target=args.unit_target.expanduser().resolve(),
            armed=args.arm,
            apply=args.apply,
            activate=args.activate,
        )
    except GuardianConfigurationError as exc:
        print(f"Erreur: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
