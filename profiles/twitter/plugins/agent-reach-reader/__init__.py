"""Outils Hermes de lecture X adossés à l'installation Agent Reach de l'hôte."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any

import yaml


PLUGIN_NAME = "agent-reach-reader"
TWITTER = Path.home() / ".local" / "bin" / "twitter"
X_CONFIG = Path.home() / ".agent-reach" / "config.yaml"
MAX_RESULTS = 20
MAX_QUERY_LENGTH = 512
MAX_OUTPUT_LENGTH = 200_000
COMMAND_TIMEOUT = 45
ACCOUNT_CACHE_SECONDS = 300
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TWEET_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]+/status/(\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
STRUCTURED_TOOL_NAMES = (
    "x_account_status",
    "x_search_tweets",
    "x_user_profile",
    "x_user_posts",
    "x_get_tweet",
    "x_home_feed",
)
SHELL_LIKE_TOOLS = {
    "terminal",
    "shell_exec",
    "execute_command",
    "execute_code",
}
STRUCTURED_TOOL_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(map(re.escape, STRUCTURED_TOOL_NAMES))
    + r")(?![A-Za-z0-9_])"
)

_account_cache: dict[str, Any] = {"username": "", "checked_at": 0.0}


STATUS_SCHEMA = {
    "name": "x_account_status",
    "description": (
        "Vérifie en lecture seule le compte X connecté via Agent Reach. "
        "N'affiche jamais les cookies de session."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

SEARCH_SCHEMA = {
    "name": "x_search_tweets",
    "description": "Recherche des posts publics sur X via Agent Reach, en lecture seule.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_LENGTH},
            "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
            "search_type": {"type": "string", "enum": ["top", "latest"]},
            "language": {
                "type": "string",
                "description": "Code de langue ISO court, par exemple fr ou en.",
                "pattern": "^[A-Za-z]{2,8}$",
            },
            "since": {"type": "string", "format": "date"},
            "until": {"type": "string", "format": "date"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

USER_SCHEMA = {
    "name": "x_user_profile",
    "description": "Lit le profil public d'un compte X, sans le modifier.",
    "parameters": {
        "type": "object",
        "properties": {"username": {"type": "string", "minLength": 1, "maxLength": 16}},
        "required": ["username"],
        "additionalProperties": False,
    },
}

USER_POSTS_SCHEMA = {
    "name": "x_user_posts",
    "description": "Lit les posts récents d'un compte X, sans interaction ni modification.",
    "parameters": {
        "type": "object",
        "properties": {
            "username": {"type": "string", "minLength": 1, "maxLength": 16},
            "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
        },
        "required": ["username"],
        "additionalProperties": False,
    },
}

TWEET_SCHEMA = {
    "name": "x_get_tweet",
    "description": "Lit un post X et un petit nombre de réponses, sans interaction.",
    "parameters": {
        "type": "object",
        "properties": {
            "tweet": {
                "type": "string",
                "description": "Identifiant numérique du post ou URL x.com/twitter.com.",
                "minLength": 1,
                "maxLength": 300,
            },
            "max_replies": {"type": "integer", "minimum": 0, "maximum": MAX_RESULTS},
        },
        "required": ["tweet"],
        "additionalProperties": False,
    },
}

FEED_SCHEMA = {
    "name": "x_home_feed",
    "description": "Lit un petit extrait du fil X connecté, sans interaction.",
    "parameters": {
        "type": "object",
        "properties": {
            "feed_type": {"type": "string", "enum": ["following", "for-you"]},
            "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
        },
        "additionalProperties": False,
    },
}


class ReaderConfigurationError(RuntimeError):
    """La lecture X n'est pas suffisamment configurée pour être sûre."""


def _normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def _active_profile_is_twitter() -> bool:
    """Le plugin est global au processus, mais strictement limité au profil Twitter."""

    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).name == "twitter"
    except ImportError:
        return os.getenv("HERMES_PROFILE", "").strip().lower() == "twitter"


def _profile_secret(name: str) -> str:
    """Lit le secret du profil actif sans consulter l'environnement d'un autre profil."""

    try:
        from agent.secret_scope import get_secret

        return str(get_secret(name, "") or "")
    except ImportError:
        return os.getenv(name, "")


def _load_configuration() -> dict[str, str]:
    if not _active_profile_is_twitter():
        raise ReaderConfigurationError("Outils X indisponibles hors du profil twitter")
    expected = _normalize_username(_profile_secret("XACTIONS_EXPECTED_USERNAME"))
    if not expected:
        raise ReaderConfigurationError(
            "XACTIONS_EXPECTED_USERNAME manque dans le .env du profil twitter"
        )
    if not X_CONFIG.is_file():
        raise ReaderConfigurationError(
            "Cookies X absents : configurer Agent Reach directement sur le serveur"
        )
    if X_CONFIG.stat().st_mode & 0o077:
        raise ReaderConfigurationError(f"Permissions trop ouvertes sur {X_CONFIG}; chmod 600 requis")

    raw = yaml.safe_load(X_CONFIG.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ReaderConfigurationError("Configuration Agent Reach invalide")
    auth_token = str(raw.get("twitter_auth_token") or "").strip()
    ct0 = str(raw.get("twitter_ct0") or "").strip()
    if not auth_token or not ct0:
        raise ReaderConfigurationError("Cookies X Agent Reach incomplets")
    if not TWITTER.is_file():
        raise ReaderConfigurationError("Façade Twitter Agent Reach absente sur l'hôte")
    return {"expected_username": expected, "auth_token": auth_token, "ct0": ct0}


def _safe_error(text: str, secrets: tuple[str, ...] = ()) -> str:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "[REDACTED]")
    safe = re.sub(
        r"(?i)(auth_token|ct0)(\s*[=:]\s*)[^\s;,\"']+",
        r"\1\2[REDACTED]",
        safe,
    )
    return safe.strip()[:2000]


def _run_twitter(arguments: list[str]) -> dict[str, Any]:
    try:
        config = _load_configuration()
        completed = subprocess.run(
            [str(TWITTER), "--compact", *arguments],
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, yaml.YAMLError, subprocess.TimeoutExpired, ReaderConfigurationError) as exc:
        return {"ok": False, "error": _safe_error(str(exc))}

    secrets = (config["auth_token"], config["ct0"])
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout or f"code {completed.returncode}"
        return {"ok": False, "error": _safe_error(detail, secrets)}
    if len(completed.stdout) > MAX_OUTPUT_LENGTH:
        return {"ok": False, "error": "Réponse X trop volumineuse; réduire max_results"}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Réponse JSON X invalide"}
    # twitter-cli 0.8.5 renvoie un objet enveloppé pour ``status`` mais une
    # liste JSON brute pour les collections (search, feed, user-posts). Les
    # handlers Hermes exposent toujours un objet homogène au modèle.
    if isinstance(payload, list):
        return {"ok": True, "data": payload}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Réponse X inattendue"}
    return payload


def _account_username(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return ""
    return _normalize_username(str(user.get("username") or user.get("screenName") or ""))


def _verified_status(*, use_cache: bool = True) -> dict[str, Any]:
    try:
        expected = _load_configuration()["expected_username"]
    except (OSError, yaml.YAMLError, ReaderConfigurationError) as exc:
        return {"ok": False, "error": _safe_error(str(exc))}

    if (
        use_cache
        and _account_cache["username"] == expected
        and time.monotonic() - float(_account_cache["checked_at"]) < ACCOUNT_CACHE_SECONDS
    ):
        return {"ok": True, "account": f"@{expected}", "cached": True}

    status = _run_twitter(["status", "--json"])
    if not status.get("ok"):
        return status
    actual = _account_username(status)
    if actual != expected:
        return {
            "ok": False,
            "error": (
                "Compte X inattendu : Agent Reach n'est pas connecté au compte "
                f"@{expected} configuré pour ce profil"
            ),
        }
    _account_cache.update(username=expected, checked_at=time.monotonic())
    return {"ok": True, "account": f"@{actual}", "authenticated": True}


def _require_verified_account() -> dict[str, Any] | None:
    status = _verified_status()
    return None if status.get("ok") else status


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _handle_status(_: dict[str, Any] | None = None, **__: Any) -> str:
    return _json_result(_verified_status(use_cache=False))


def _bounded_count(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RESULTS:
        raise ValueError(f"{key} doit être compris entre 0 et {MAX_RESULTS}")
    return value


def _validate_date(value: Any, key: str) -> str:
    if not value:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} doit être une date YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} doit être une date YYYY-MM-DD") from exc
    return value


def _handle_search(args: dict[str, Any], **__: Any) -> str:
    try:
        query = str(args.get("query") or "").strip()
        if not query or len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query doit contenir entre 1 et {MAX_QUERY_LENGTH} caractères")
        count = _bounded_count(args, "max_results", 10)
        if count < 1:
            raise ValueError("max_results doit être supérieur à zéro")
        search_type = str(args.get("search_type") or "top")
        if search_type not in {"top", "latest"}:
            raise ValueError("search_type invalide")
        command = ["search", query, "--type", search_type, "--max", str(count)]
        language = str(args.get("language") or "").strip().lower()
        if language:
            if not re.fullmatch(r"[a-z]{2,8}", language):
                raise ValueError("language invalide")
            command.extend(["--lang", language])
        since = _validate_date(args.get("since"), "since")
        until = _validate_date(args.get("until"), "until")
        if since:
            command.extend(["--since", since])
        if until:
            command.extend(["--until", until])
        command.append("--json")
    except (AttributeError, TypeError, ValueError) as exc:
        return _json_result({"ok": False, "error": str(exc)})
    blocked = _require_verified_account()
    return _json_result(blocked or _run_twitter(command))


def _handle_user(args: dict[str, Any], **__: Any) -> str:
    username = _normalize_username(str(args.get("username") or ""))
    if not HANDLE_RE.fullmatch(username):
        return _json_result({"ok": False, "error": "username X invalide"})
    blocked = _require_verified_account()
    return _json_result(blocked or _run_twitter(["user", username, "--json"]))


def _handle_user_posts(args: dict[str, Any], **__: Any) -> str:
    username = _normalize_username(str(args.get("username") or ""))
    if not HANDLE_RE.fullmatch(username):
        return _json_result({"ok": False, "error": "username X invalide"})
    try:
        count = _bounded_count(args, "max_results", 10)
        if count < 1:
            raise ValueError("max_results doit être supérieur à zéro")
    except ValueError as exc:
        return _json_result({"ok": False, "error": str(exc)})
    blocked = _require_verified_account()
    return _json_result(
        blocked or _run_twitter(["user-posts", username, "--max", str(count), "--json"])
    )


def _tweet_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if candidate.isdigit():
        return candidate
    match = TWEET_URL_RE.fullmatch(candidate)
    if not match:
        raise ValueError("tweet doit être un identifiant numérique ou une URL X valide")
    return match.group(1)


def _handle_tweet(args: dict[str, Any], **__: Any) -> str:
    try:
        tweet_id = _tweet_id(args.get("tweet"))
        replies = _bounded_count(args, "max_replies", 10)
    except ValueError as exc:
        return _json_result({"ok": False, "error": str(exc)})
    blocked = _require_verified_account()
    return _json_result(
        blocked or _run_twitter(["tweet", tweet_id, "--max", str(replies), "--json"])
    )


def _handle_feed(args: dict[str, Any], **__: Any) -> str:
    feed_type = str(args.get("feed_type") or "following")
    if feed_type not in {"following", "for-you"}:
        return _json_result({"ok": False, "error": "feed_type invalide"})
    try:
        count = _bounded_count(args, "max_results", 10)
        if count < 1:
            raise ValueError("max_results doit être supérieur à zéro")
    except ValueError as exc:
        return _json_result({"ok": False, "error": str(exc)})
    blocked = _require_verified_account()
    return _json_result(
        blocked or _run_twitter(["feed", "--type", feed_type, "--max", str(count), "--json"])
    )


def _check_available() -> bool:
    try:
        _load_configuration()
    except (OSError, yaml.YAMLError, ReaderConfigurationError):
        return False
    return True


def _contains_structured_tool_name(value: Any) -> bool:
    """Détecte un nom d'outil X tenté depuis du code ou un shell."""

    if isinstance(value, str):
        return bool(STRUCTURED_TOOL_IN_TEXT_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_structured_tool_name(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_structured_tool_name(item) for item in value)
    return False


def _structured_tool_routing_hook(
    tool_name: str = "",
    args: Any = None,
    **_: Any,
) -> dict[str, str] | None:
    """Empêche le modèle de confondre une fonction Hermes avec un binaire."""

    if (
        _active_profile_is_twitter()
        and tool_name in SHELL_LIKE_TOOLS
        and _contains_structured_tool_name(args)
    ):
        return {
            "action": "block",
            "message": (
                "Nom d'outil X utilisé comme commande système. Appelle directement "
                "l'outil Hermes structuré demandé (par exemple x_account_status) "
                "avec un tool call ; ne l'exécute pas via terminal ou execute_code."
            ),
        }
    return None


def _stamp_multiplex_profile_route(
    event: Any = None,
    gateway: Any = None,
    **_: Any,
) -> None:
    """Rétablit la route de profil oubliée par certaines commandes slash.

    Les messages Discord ordinaires passent ``guild_id`` à ``build_source`` et
    reçoivent leur profil immédiatement. Certaines commandes slash construites
    par l'adaptateur omettent cet identifiant ; leur source reste alors dans
    ``agent:main``. Ce hook s'exécute avant le calcul de la clé de session et
    réutilise le routeur officiel du gateway.
    """

    source = getattr(event, "source", None)
    if source is None or getattr(source, "profile", None):
        return None
    raw_event = getattr(event, "raw_message", None)
    if not getattr(source, "guild_id", None):
        guild_id = getattr(raw_event, "guild_id", None)
        if guild_id is not None:
            source.guild_id = str(guild_id)
    if not getattr(source, "parent_chat_id", None):
        channel = getattr(raw_event, "channel", None)
        parent_id = getattr(channel, "parent_id", None)
        if parent_id is not None:
            source.parent_chat_id = str(parent_id)
    resolver = getattr(gateway, "_profile_name_for_source", None)
    if not callable(resolver):
        return None
    profile = resolver(source)
    if profile:
        source.profile = profile
    return None


def register(ctx) -> None:
    tools = (
        (STATUS_SCHEMA, _handle_status),
        (SEARCH_SCHEMA, _handle_search),
        (USER_SCHEMA, _handle_user),
        (USER_POSTS_SCHEMA, _handle_user_posts),
        (TWEET_SCHEMA, _handle_tweet),
        (FEED_SCHEMA, _handle_feed),
    )
    for schema, handler in tools:
        ctx.register_tool(
            name=schema["name"],
            toolset="agent_reach_reader",
            schema=schema,
            handler=handler,
            check_fn=_check_available,
            emoji="🔎",
        )
    ctx.register_hook("pre_tool_call", _structured_tool_routing_hook)
    ctx.register_hook("pre_gateway_dispatch", _stamp_multiplex_profile_route)
