#!/usr/bin/env python3
"""Configure le routage Discord natif de Hermes de façon contrôlée.

Par défaut, ce script valide puis affiche un plan. ``--apply`` est nécessaire pour
créer les profils et écrire ``config.yaml``. Les secrets ne sont jamais lus ni copiés.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


MANAGED_ROUTE_PREFIX = "discord-channel/"
PLACEHOLDER_PREFIX = "REPLACE_WITH_"
DEFAULT_PROFILE = "default"
SNOWFLAKE_RE = re.compile(r"^[0-9]{15,22}$")
PROFILE_RE = re.compile(r"^[a-z][a-z0-9]{1,31}$")
TOOLSET_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROFILE_GATEWAY_ENV_PREFIXES = ("DISCORD_", "TELEGRAM_")


class ConfigurationError(ValueError):
    """Le manifeste ne peut pas être appliqué sans correction."""


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Fichier introuvable : {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"YAML invalide dans {path} : {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigurationError(f"{path} doit contenir un objet YAML à la racine")
    return data


def _is_valid_snowflake(value: Any, *, allow_placeholders: bool) -> bool:
    text = str(value or "")
    if allow_placeholders and text.startswith(PLACEHOLDER_PREFIX):
        return True
    return bool(SNOWFLAKE_RE.fullmatch(text))


def validate_manifest(
    manifest: dict[str, Any],
    repo_root: Path,
    *,
    allow_placeholders: bool = False,
) -> list[dict[str, Any]]:
    """Valide le manifeste et renvoie sa liste normalisée de salons."""

    errors: list[str] = []
    if manifest.get("version") != 1:
        errors.append("version doit valoir 1")

    discord = manifest.get("discord")
    if not isinstance(discord, dict):
        raise ConfigurationError("discord doit être un objet YAML")

    guild_id = discord.get("guild_id")
    if not _is_valid_snowflake(guild_id, allow_placeholders=allow_placeholders):
        errors.append("discord.guild_id doit être un identifiant Discord numérique")

    raw_channels = discord.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        errors.append("discord.channels doit contenir au moins un salon")
        raw_channels = []

    channels: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    seen_profiles: set[str] = set()

    for index, raw in enumerate(raw_channels):
        prefix = f"discord.channels[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix} doit être un objet")
            continue

        channel = dict(raw)
        name = str(channel.get("name") or "").strip()
        channel_id = str(channel.get("channel_id") or "").strip()
        profile = str(channel.get("profile") or "").strip()

        if not name:
            errors.append(f"{prefix}.name est obligatoire")
        elif name in seen_names:
            errors.append(f"nom de salon dupliqué : {name}")
        seen_names.add(name)

        if not _is_valid_snowflake(channel_id, allow_placeholders=allow_placeholders):
            errors.append(f"{prefix}.channel_id doit être un identifiant Discord numérique")
        elif channel_id in seen_ids:
            errors.append(f"identifiant de salon dupliqué : {channel_id}")
        seen_ids.add(channel_id)

        if not PROFILE_RE.fullmatch(profile):
            errors.append(
                f"{prefix}.profile doit contenir uniquement des lettres minuscules "
                "et des chiffres"
            )
        elif profile in seen_profiles:
            errors.append(f"profil utilisé par plusieurs salons : {profile}")
        seen_profiles.add(profile)

        soul_path = repo_root / "profiles" / profile / "SOUL.md"
        if profile and profile != DEFAULT_PROFILE and not soul_path.is_file():
            errors.append(f"mission absente pour le profil {profile} : {soul_path}")

        description = str(channel.get("description") or "").strip()
        if not description:
            errors.append(f"{prefix}.description est obligatoire")

        for option in ("respond_without_mention", "use_threads"):
            if option in channel and not isinstance(channel[option], bool):
                errors.append(f"{prefix}.{option} doit être true ou false")

        toolsets = channel.get("toolsets")
        if toolsets is not None:
            if not isinstance(toolsets, list) or not toolsets:
                errors.append(f"{prefix}.toolsets doit être une liste non vide")
                toolsets = []
            else:
                normalized_toolsets = [str(item).strip() for item in toolsets]
                invalid = [
                    item
                    for item in normalized_toolsets
                    if not TOOLSET_RE.fullmatch(item)
                ]
                if invalid:
                    errors.append(
                        f"{prefix}.toolsets contient un nom invalide : {invalid[0]}"
                    )
                if len(set(normalized_toolsets)) != len(normalized_toolsets):
                    errors.append(f"{prefix}.toolsets contient un doublon")
                toolsets = normalized_toolsets

        channel.update(
            name=name,
            channel_id=channel_id,
            profile=profile,
            description=description,
            respond_without_mention=channel.get("respond_without_mention", False),
            use_threads=channel.get("use_threads", True),
            toolsets=toolsets,
        )
        channels.append(channel)

    if errors:
        raise ConfigurationError("\n- " + "\n- ".join(errors))
    return channels


def _as_id_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def render_config(
    existing: dict[str, Any],
    manifest: dict[str, Any],
    channels: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fusionne uniquement les clés gérées, en préservant le reste de Hermes."""

    result = copy.deepcopy(existing)
    gateway = result.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        raise ConfigurationError("gateway existe déjà mais n’est pas un objet YAML")

    gateway["multiplex_profiles"] = True
    if "multiplex_profiles" in result:
        # La forme top-level prend la priorité dans certaines versions Hermes.
        result["multiplex_profiles"] = True

    top_routes = result.get("profile_routes")
    nested_routes = gateway.get("profile_routes")
    source_routes = top_routes if isinstance(top_routes, list) else nested_routes
    if not isinstance(source_routes, list):
        source_routes = []

    old_managed_ids = {
        str(route.get("chat_id"))
        for route in source_routes
        if isinstance(route, dict)
        and str(route.get("name", "")).startswith(MANAGED_ROUTE_PREFIX)
        and route.get("chat_id") is not None
    }
    preserved_routes = [
        route
        for route in source_routes
        if not (
            isinstance(route, dict)
            and str(route.get("name", "")).startswith(MANAGED_ROUTE_PREFIX)
        )
    ]

    guild_id = str(manifest["discord"]["guild_id"])
    managed_routes = [
        {
            "name": f"{MANAGED_ROUTE_PREFIX}{channel['name']}",
            "platform": "discord",
            "guild_id": guild_id,
            "chat_id": channel["channel_id"],
            "profile": channel["profile"],
        }
        for channel in channels
        if channel["profile"] != DEFAULT_PROFILE
    ]
    routes = preserved_routes + managed_routes
    gateway["profile_routes"] = routes
    if "profile_routes" in result:
        result["profile_routes"] = routes

    discord_cfg = result.setdefault("discord", {})
    if not isinstance(discord_cfg, dict):
        raise ConfigurationError("discord existe déjà mais n’est pas un objet YAML")

    managed_ids = [channel["channel_id"] for channel in channels]
    free_ids = [
        channel["channel_id"]
        for channel in channels
        if channel["respond_without_mention"]
    ]
    no_thread_ids = [
        channel["channel_id"] for channel in channels if not channel["use_threads"]
    ]

    # Garder les salons externes à ce dépôt, tout en supprimant ses anciennes valeurs.
    previous_free = [
        value
        for value in _as_id_list(discord_cfg.get("free_response_channels"))
        if value not in old_managed_ids
    ]
    previous_no_thread = [
        value
        for value in _as_id_list(discord_cfg.get("no_thread_channels"))
        if value not in old_managed_ids
    ]

    discord_cfg["require_mention"] = True
    discord_cfg["auto_thread"] = True
    discord_cfg["free_response_channels"] = _unique(previous_free + free_ids)
    discord_cfg["no_thread_channels"] = _unique(previous_no_thread + no_thread_ids)

    if manifest["discord"].get("restrict_to_managed_channels", True):
        discord_cfg["allowed_channels"] = managed_ids

    result["group_sessions_per_user"] = bool(
        manifest["discord"].get("group_sessions_per_user", True)
    )
    return result


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup(path: Path, timestamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak.discord-{timestamp}")
    shutil.copy2(path, backup)
    return backup


def _write_yaml_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    temporary = path.with_name(f".{path.name}.discord.tmp")
    temporary.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    temporary.chmod(mode)
    os.replace(temporary, path)


def _env_has_value(name: str, env_path: Path) -> bool:
    """Vérifie la présence d’un secret sans jamais l’afficher."""

    if os.getenv(name, "").strip():
        return True
    if not env_path.is_file():
        return False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = raw_value.strip().strip("\"'")
        return bool(value) and not value.startswith(PLACEHOLDER_PREFIX)
    return False


def _strip_profile_gateway_credentials(env_path: Path, timestamp: str) -> list[str]:
    """Retire des profils les transports possédés par le gateway principal.

    Les profils clonés conservent les credentials des providers IA, mais ne doivent
    pas démarrer leurs propres bots Telegram/Discord avec les mêmes tokens.
    """

    if not env_path.is_file():
        return []
    original = env_path.read_text(encoding="utf-8")
    kept: list[str] = []
    removed: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if (
            stripped
            and not stripped.startswith("#")
            and any(key.startswith(prefix) for prefix in PROFILE_GATEWAY_ENV_PREFIXES)
        ):
            removed.append(key)
            continue
        kept.append(line)

    if not removed:
        return []
    backup = _backup(env_path, timestamp)
    if backup:
        print(f"Sauvegarde : {backup}")
    mode = env_path.stat().st_mode & 0o777
    temporary = env_path.with_name(f".{env_path.name}.discord.tmp")
    temporary.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, env_path)
    print(
        f"Credentials de gateway retirés de {env_path} : "
        + ", ".join(sorted(set(removed)))
    )
    return removed


def _profile_plugin_names(source_profile: Path) -> list[str]:
    plugins_root = source_profile / "plugins"
    if not plugins_root.is_dir():
        return []
    return sorted(
        child.name
        for child in plugins_root.iterdir()
        if child.is_dir()
        and (child / "plugin.yaml").is_file()
        and (child / "__init__.py").is_file()
    )


def _multiplex_runtime_plugins(
    *, channels: list[dict[str, Any]], repo_root: Path
) -> dict[str, Path]:
    """Sélectionne les plugins à charger globalement dans un gateway multiplexé."""

    selected: dict[str, Path] = {}
    for channel in channels:
        source_profile = repo_root / "profiles" / channel["profile"]
        plugins_root = source_profile / "plugins"
        if not plugins_root.is_dir():
            continue
        for name in _profile_plugin_names(source_profile):
            source = plugins_root / name
            manifest = _load_yaml(source / "plugin.yaml")
            if manifest.get("multiplex_global") is not True:
                continue
            previous = selected.get(name)
            if previous is not None and previous.resolve() != source.resolve():
                raise ConfigurationError(
                    f"Plugin multiplexé dupliqué entre profils : {name}"
                )
            selected[name] = source
    return selected


def _enable_profile_plugins(config: dict[str, Any], names: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    plugins = result.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ConfigurationError("plugins existe déjà mais n’est pas un objet YAML")
    enabled = plugins.get("enabled")
    if enabled is None:
        enabled = []
    if not isinstance(enabled, list):
        raise ConfigurationError("plugins.enabled existe déjà mais n’est pas une liste")
    plugins["enabled"] = _unique(
        [str(item) for item in enabled if str(item).strip()] + names
    )
    return result


def _set_profile_discord_toolsets(
    config: dict[str, Any], toolsets: list[str] | None
) -> dict[str, Any]:
    """Applique une surface d'outils Discord étroite au profil concerné."""

    result = copy.deepcopy(config)
    if toolsets is None:
        return result
    platform_toolsets = result.setdefault("platform_toolsets", {})
    if not isinstance(platform_toolsets, dict):
        raise ConfigurationError(
            "platform_toolsets existe déjà mais n’est pas un objet YAML"
        )
    platform_toolsets["discord"] = list(toolsets)
    return result


def _relocate_scannable_plugin_backups(target_plugins: Path) -> None:
    """Sort les sauvegardes du dossier que le chargeur Hermes exécute."""

    if not target_plugins.is_dir():
        return
    backup_root = target_plugins.parent / "backups" / "plugins"
    for legacy in sorted(target_plugins.glob("*.bak.discord-*")):
        if not legacy.is_dir():
            continue
        backup_root.mkdir(parents=True, exist_ok=True)
        destination = backup_root / legacy.name
        if destination.exists():
            raise ConfigurationError(
                f"Impossible de déplacer la sauvegarde de plugin : {destination} existe"
            )
        legacy.rename(destination)
        print(f"Sauvegarde de plugin isolée : {destination}")


def _plugin_backup_path(target_plugins: Path, name: str, timestamp: str) -> Path:
    backup_root = target_plugins.parent / "backups" / "plugins"
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root / f"{name}.bak.discord-{timestamp}"


def _deploy_multiplex_runtime_plugins(
    *,
    channels: list[dict[str, Any]],
    repo_root: Path,
    hermes_home: Path,
    timestamp: str,
    update_config: bool,
) -> list[str]:
    """Déploie dans le home principal les plugins requis par le processus gateway."""

    selected = _multiplex_runtime_plugins(channels=channels, repo_root=repo_root)
    if not selected:
        return []

    target_plugins = hermes_home / "plugins"
    target_plugins.mkdir(parents=True, exist_ok=True)
    _relocate_scannable_plugin_backups(target_plugins)
    for name, source in selected.items():
        target = target_plugins / name
        staging = target_plugins / f".{name}.discord.tmp"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging)

        backup = _plugin_backup_path(target_plugins, name, timestamp)
        moved_existing = False
        try:
            if target.exists():
                if backup.exists():
                    raise ConfigurationError(
                        f"Sauvegarde de plugin gateway déjà présente : {backup}"
                    )
                target.rename(backup)
                moved_existing = True
                print(f"Sauvegarde : {backup}")
            staging.rename(target)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            if moved_existing and backup.exists() and not target.exists():
                backup.rename(target)
            raise
        print(f"Plugin gateway déployé : {target}")

    names = sorted(selected)
    if update_config:
        config_path = hermes_home / "config.yaml"
        current = _load_yaml(config_path) if config_path.exists() else {}
        rendered = _enable_profile_plugins(current, names)
        backup = _backup(config_path, timestamp)
        if backup:
            print(f"Sauvegarde : {backup}")
        _write_yaml_atomic(config_path, rendered)
        print(f"Plugins gateway activés dans : {config_path}")
    return names


def _deploy_profile_plugins(
    *,
    source_profile: Path,
    target_profile: Path,
    timestamp: str,
    discord_toolsets: list[str] | None = None,
) -> None:
    names = _profile_plugin_names(source_profile)
    if not names and discord_toolsets is None:
        return

    if names:
        target_plugins = target_profile / "plugins"
        target_plugins.mkdir(parents=True, exist_ok=True)
        _relocate_scannable_plugin_backups(target_plugins)
        for name in names:
            source = source_profile / "plugins" / name
            target = target_plugins / name
            staging = target_plugins / f".{name}.discord.tmp"
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source, staging)

            backup = _plugin_backup_path(target_plugins, name, timestamp)
            moved_existing = False
            try:
                if target.exists():
                    if backup.exists():
                        raise ConfigurationError(
                            f"Sauvegarde de plugin déjà présente : {backup}"
                        )
                    target.rename(backup)
                    moved_existing = True
                    print(f"Sauvegarde : {backup}")
                staging.rename(target)
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging)
                if moved_existing and backup.exists() and not target.exists():
                    backup.rename(target)
                raise
            print(f"Plugin déployé : {target}")

    config_path = target_profile / "config.yaml"
    current = _load_yaml(config_path) if config_path.exists() else {}
    rendered = _enable_profile_plugins(current, names)
    rendered = _set_profile_discord_toolsets(rendered, discord_toolsets)
    backup = _backup(config_path, timestamp)
    if backup:
        print(f"Sauvegarde : {backup}")
    _write_yaml_atomic(config_path, rendered)
    print(f"Plugins activés dans : {config_path}")


def _create_or_update_profiles(
    *,
    channels: list[dict[str, Any]],
    repo_root: Path,
    hermes_home: Path,
    hermes_bin: str,
    timestamp: str,
    update_gateway_plugin_config: bool = True,
) -> None:
    profiles_root = hermes_home / "profiles"
    profiles_root.mkdir(parents=True, exist_ok=True)

    for channel in channels:
        profile = channel["profile"]
        if profile == DEFAULT_PROFILE:
            # Un salon sans route explicite appartient au home principal et à la
            # session agent:main. Il n'existe pas de dossier profiles/default.
            continue
        target = profiles_root / profile
        if not target.exists():
            subprocess.run(
                [
                    hermes_bin,
                    "profile",
                    "create",
                    profile,
                    "--clone-from",
                    "default",
                    "--no-alias",
                    "--description",
                    channel["description"],
                ],
                check=True,
                env={**os.environ, "HERMES_HOME": str(hermes_home)},
            )

        source_soul = repo_root / "profiles" / profile / "SOUL.md"
        target_soul = target / "SOUL.md"
        _strip_profile_gateway_credentials(target / ".env", timestamp)
        backup = _backup(target_soul, timestamp)
        if backup:
            print(f"Sauvegarde : {backup}")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_soul, target_soul)
        print(f"Mission déployée : {target_soul}")
        _deploy_profile_plugins(
            source_profile=repo_root / "profiles" / profile,
            target_profile=target,
            timestamp=timestamp,
            discord_toolsets=channel.get("toolsets"),
        )

    _deploy_multiplex_runtime_plugins(
        channels=channels,
        repo_root=repo_root,
        hermes_home=hermes_home,
        timestamp=timestamp,
        update_config=update_gateway_plugin_config,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/discord-channels.yaml"),
        help="manifeste des salons Discord",
    )
    parser.add_argument(
        "--hermes-home",
        type=Path,
        default=Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser(),
        help="dossier HERMES_HOME par défaut",
    )
    parser.add_argument("--hermes-bin", default="hermes", help="exécutable Hermes")
    parser.add_argument("--check", action="store_true", help="valider puis quitter")
    parser.add_argument(
        "--check-template",
        action="store_true",
        help="valider un exemple contenant des placeholders",
    )
    parser.add_argument("--apply", action="store_true", help="écrire et créer les profils")
    parser.add_argument(
        "--profiles-only",
        action="store_true",
        help="déployer seulement les profils et plugins, sans modifier le gateway",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="redémarrer le gateway après application",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.restart and not args.apply:
        print("Erreur : --restart nécessite --apply", file=sys.stderr)
        return 2
    if args.restart and args.profiles_only:
        print("Erreur : --restart est incompatible avec --profiles-only", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = args.manifest.expanduser().resolve()
    hermes_home = args.hermes_home.expanduser().resolve()

    try:
        manifest = _load_yaml(manifest_path)
        channels = validate_manifest(
            manifest,
            repo_root,
            allow_placeholders=args.check_template or args.profiles_only,
        )
    except ConfigurationError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    if args.check or args.check_template:
        print(f"Configuration valide : {len(channels)} salon(s), {len(channels)} profil(s)")
        return 0

    if args.profiles_only:
        print("Plan des profils :")
        for channel in channels:
            print(f"- {channel['profile']} · {channel['description']}")
        if not args.apply:
            print("Aucun fichier modifié. Relancer avec --apply après vérification.")
            return 0
        try:
            _create_or_update_profiles(
                channels=channels,
                repo_root=repo_root,
                hermes_home=hermes_home,
                hermes_bin=args.hermes_bin,
                timestamp=_timestamp(),
            )
        except (ConfigurationError, OSError, subprocess.CalledProcessError) as exc:
            print(f"Échec de l’application : {exc}", file=sys.stderr)
            return 1
        return 0

    config_path = hermes_home / "config.yaml"
    try:
        existing = _load_yaml(config_path) if config_path.exists() else {}
        rendered = render_config(existing, manifest, channels)
        runtime_plugin_names = sorted(
            _multiplex_runtime_plugins(channels=channels, repo_root=repo_root)
        )
        rendered = _enable_profile_plugins(rendered, runtime_plugin_names)
    except ConfigurationError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    print("Plan Discord :")
    for channel in channels:
        mention = "sans mention" if channel["respond_without_mention"] else "avec @mention"
        threads = "threads" if channel["use_threads"] else "réponses directes"
        print(
            f"- {channel['name']} ({channel['channel_id']}) -> "
            f"profil {channel['profile']} · {mention} · {threads}"
        )

    if not args.apply:
        print("Aucun fichier modifié. Relancer avec --apply après vérification.")
        return 0

    if args.restart:
        missing_env = [
            name
            for name in ("DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_USERS")
            if not _env_has_value(name, hermes_home / ".env")
        ]
        if missing_env:
            print(
                "Redémarrage refusé, variable(s) Discord absente(s) : "
                + ", ".join(missing_env),
                file=sys.stderr,
            )
            return 2

    timestamp = _timestamp()
    try:
        _create_or_update_profiles(
            channels=channels,
            repo_root=repo_root,
            hermes_home=hermes_home,
            hermes_bin=args.hermes_bin,
            timestamp=timestamp,
            update_gateway_plugin_config=False,
        )
        backup = _backup(config_path, timestamp)
        if backup:
            print(f"Sauvegarde : {backup}")
        _write_yaml_atomic(config_path, rendered)
        print(f"Configuration écrite : {config_path}")

        if args.restart:
            subprocess.run(
                [args.hermes_bin, "gateway", "restart"],
                check=True,
                env={**os.environ, "HERMES_HOME": str(hermes_home)},
            )
            print("Gateway Hermes redémarré.")
    except (ConfigurationError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Échec de l’application : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
