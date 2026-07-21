"""Plugin Hermes d'écriture X à surface volontairement minimale."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


POST_TOOL_NAME = "xactions_post_tweet"
REPLY_TOOL_NAME = "xactions_reply_tweet"
LIKE_TOOL_NAME = "xactions_like_tweet"
FOLLOW_TOOL_NAME = "xactions_follow_user"
# Compatibilité avec les tests et les anciennes installations du plugin.
TOOL_NAME = POST_TOOL_NAME
INTERACTION_TOOL_NAMES = {REPLY_TOOL_NAME, LIKE_TOOL_NAME, FOLLOW_TOOL_NAME}
X_CONFIG = Path.home() / ".agent-reach" / "config.yaml"
X_ACTIONS_ROOT = (
    Path.home() / ".local" / "share" / "hermes-social" / "xactions"
)
RUNNER = Path(__file__).with_name("runner.mjs")
MAX_LENGTH = 280
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TWEET_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]+/status/(\d+)"
    r"(?:[/?#].*)?$",
    re.IGNORECASE,
)

MUTATING_TWITTER_COMMAND = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:\S*/)?twitter(?:-real)?\s+"
    r"(?:post|reply|quote|delete|like|unlike|retweet|unretweet|bookmark|"
    r"unbookmark|follow|unfollow)(?:\s|$)",
    re.IGNORECASE,
)

POST_SCHEMA = {
    "name": POST_TOOL_NAME,
    "description": (
        "Publie un nouveau post texte sur le compte X configuré. Toute invocation "
        "est suspendue par Hermes jusqu’à l’approbation humaine du texte exact. "
        "Ne permet ni suppression ni média."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Texte exact à publier, limité à 280 caractères.",
                "minLength": 1,
                "maxLength": MAX_LENGTH,
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}
SCHEMA = POST_SCHEMA

REPLY_SCHEMA = {
    "name": REPLY_TOOL_NAME,
    "description": (
        "Répond à un post X avec un texte de 280 caractères maximum. "
        "Action unitaire exécutée directement à la demande de l'utilisateur, sans "
        "seconde demande d'approbation Hermes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tweet": {
                "type": "string",
                "description": "Identifiant numérique ou URL du post auquel répondre.",
                "minLength": 1,
                "maxLength": 300,
            },
            "text": {
                "type": "string",
                "description": "Texte exact de la réponse.",
                "minLength": 1,
                "maxLength": MAX_LENGTH,
            },
        },
        "required": ["tweet", "text"],
        "additionalProperties": False,
    },
}

LIKE_SCHEMA = {
    "name": LIKE_TOOL_NAME,
    "description": (
        "Like un post X unique demandé par l'utilisateur, sans seconde demande "
        "d'approbation Hermes. N'accepte aucune opération en masse."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tweet": {
                "type": "string",
                "description": "Identifiant numérique ou URL du post à liker.",
                "minLength": 1,
                "maxLength": 300,
            }
        },
        "required": ["tweet"],
        "additionalProperties": False,
    },
}

FOLLOW_SCHEMA = {
    "name": FOLLOW_TOOL_NAME,
    "description": (
        "Suit un compte X unique demandé par l'utilisateur, sans seconde demande "
        "d'approbation Hermes. N'accepte aucune opération en masse."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "Nom du compte à suivre, avec ou sans @.",
                "minLength": 1,
                "maxLength": 16,
            }
        },
        "required": ["username"],
        "additionalProperties": False,
    },
}


class SocialConfigurationError(RuntimeError):
    """La publication n’est pas suffisamment configurée pour être sûre."""


def _normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def _active_profile_is_twitter() -> bool:
    """Le plugin est global au gateway, mais ne s'active que pour Twitter."""

    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).name == "twitter"
    except ImportError:
        return os.getenv("HERMES_PROFILE", "").strip().lower() == "twitter"


def _profile_secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret

        return str(get_secret(name, "") or "")
    except ImportError:
        return os.getenv(name, "")


def _validate_text(args: Any) -> str:
    if not isinstance(args, dict):
        raise ValueError("Les arguments doivent être un objet")
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Le texte du post est obligatoire")
    if len(text) > MAX_LENGTH:
        raise ValueError(f"Le post dépasse {MAX_LENGTH} caractères ({len(text)})")
    return text


def _tweet_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if candidate.isdigit():
        return candidate
    match = TWEET_RE.fullmatch(candidate)
    if not match:
        raise ValueError("tweet doit être un identifiant numérique ou une URL X valide")
    return match.group(1)


def _validate_username(value: Any) -> str:
    username = _normalize_username(str(value or ""))
    if not HANDLE_RE.fullmatch(username):
        raise ValueError("username X invalide")
    return username


def _validate_interaction(tool_name: str, args: Any) -> None:
    if not isinstance(args, dict):
        raise ValueError("Les arguments doivent être un objet")
    if tool_name == REPLY_TOOL_NAME:
        _tweet_id(args.get("tweet"))
        _validate_text(args)
    elif tool_name == LIKE_TOOL_NAME:
        _tweet_id(args.get("tweet"))
    elif tool_name == FOLLOW_TOOL_NAME:
        _validate_username(args.get("username"))


def _load_configuration() -> dict[str, str]:
    if not _active_profile_is_twitter():
        raise SocialConfigurationError("Publication X indisponible hors du profil twitter")
    expected_username = _normalize_username(_profile_secret("XACTIONS_EXPECTED_USERNAME"))
    if not expected_username:
        raise SocialConfigurationError(
            "XACTIONS_EXPECTED_USERNAME manque dans le .env du profil twitter"
        )

    if not X_CONFIG.is_file():
        raise SocialConfigurationError(
            "Cookies X absents : exécuter agent-reach configure twitter-cookies "
            "directement sur le serveur"
        )
    if X_CONFIG.stat().st_mode & 0o077:
        raise SocialConfigurationError(
            f"Permissions trop ouvertes sur {X_CONFIG}; exécuter chmod 600"
        )

    raw = yaml.safe_load(X_CONFIG.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SocialConfigurationError("Configuration Agent Reach invalide")

    auth_token = str(raw.get("twitter_auth_token") or "").strip()
    ct0 = str(raw.get("twitter_ct0") or "").strip()
    if not auth_token or not ct0:
        raise SocialConfigurationError(
            "Cookies X incomplets dans Agent Reach (auth_token et ct0 requis)"
        )
    return {
        "auth_token": auth_token,
        "ct0": ct0,
        "expected_username": expected_username,
    }


def _check_available() -> bool:
    try:
        _load_configuration()
    except (OSError, ValueError, SocialConfigurationError):
        return False
    return bool(shutil.which("node") and RUNNER.is_file() and X_ACTIONS_ROOT.is_dir())


def _approval_hook(
    tool_name: str = "",
    args: Any = None,
    **_: Any,
) -> dict[str, str] | None:
    if tool_name == POST_TOOL_NAME:
        try:
            text = _validate_text(args)
        except ValueError as exc:
            return {"action": "block", "message": f"Publication X refusée : {exc}"}

        if not _active_profile_is_twitter():
            return {
                "action": "block",
                "message": "Publication X refusée hors du profil twitter",
            }
        username = _normalize_username(_profile_secret("XACTIONS_EXPECTED_USERNAME"))
        account = f"@{username}" if username else "le compte X configuré"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
        return {
            "action": "approve",
            "message": (
                f"Confirmer la publication sur {account} ({len(text)} caractères) :\n\n"
                f"{text}"
            ),
            # Une approbation persistante ne peut couvrir que ce contenu exact.
            "rule_key": f"{TOOL_NAME}:{digest}",
        }

    if tool_name in INTERACTION_TOOL_NAMES:
        try:
            _validate_interaction(tool_name, args)
        except ValueError as exc:
            return {"action": "block", "message": f"Interaction X refusée : {exc}"}
        if not _active_profile_is_twitter():
            return {
                "action": "block",
                "message": "Interaction X refusée hors du profil twitter",
            }
        # L'instruction utilisateur est l'autorisation : pas de prompt Hermes
        # supplémentaire pour une interaction unitaire.
        return None

    # Empêche les contournements évidents via le binaire twitter-cli complet.
    if tool_name in {"terminal", "shell_exec", "execute_command"} and isinstance(args, dict):
        command = str(args.get("command") or args.get("cmd") or "")
        if MUTATING_TWITTER_COMMAND.search(command):
            return {
                "action": "block",
                "message": (
                    "Écriture X refusée via le terminal. Utiliser les outils "
                    "xactions structurés ; les nouveaux posts gardent l’approbation "
                    "du contenu exact."
                ),
            }
    return None


def _run_action(payload: dict[str, Any]) -> str:
    try:
        config = _load_configuration()
    except (OSError, SocialConfigurationError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    node = shutil.which("node")
    if not node or not RUNNER.is_file() or not X_ACTIONS_ROOT.is_dir():
        return json.dumps(
            {
                "success": False,
                "error": "XActions n’est pas installé ou Node.js est introuvable",
            },
            ensure_ascii=False,
        )

    env = os.environ.copy()
    env.update(
        {
            "XACTIONS_ROOT": str(X_ACTIONS_ROOT),
            "XACTIONS_AUTH_TOKEN": config["auth_token"],
            "XACTIONS_CT0": config["ct0"],
            "XACTIONS_EXPECTED_USERNAME": config["expected_username"],
        }
    )
    try:
        completed = subprocess.run(
            [node, str(RUNNER)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return json.dumps(
            {"success": False, "error": "XActions a échoué ou dépassé 45 secondes"},
            ensure_ascii=False,
        )

    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        result = {
            "success": False,
            "error": f"Réponse XActions invalide (code {completed.returncode})",
        }
    if not isinstance(result, dict):
        result = {"success": False, "error": "Réponse XActions invalide"}
    return json.dumps(result, ensure_ascii=False)


def _handle_post(args: dict[str, Any], **_: Any) -> str:
    try:
        text = _validate_text(args)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
    return _run_action({"action": "post", "text": text})


def _handle_reply(args: dict[str, Any], **_: Any) -> str:
    try:
        tweet_id = _tweet_id(args.get("tweet"))
        text = _validate_text(args)
    except (AttributeError, ValueError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
    return _run_action({"action": "reply", "tweet_id": tweet_id, "text": text})


def _handle_like(args: dict[str, Any], **_: Any) -> str:
    try:
        tweet_id = _tweet_id(args.get("tweet"))
    except (AttributeError, ValueError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
    return _run_action({"action": "like", "tweet_id": tweet_id})


def _handle_follow(args: dict[str, Any], **_: Any) -> str:
    try:
        username = _validate_username(args.get("username"))
    except (AttributeError, ValueError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
    return _run_action({"action": "follow", "username": username})


def register(ctx) -> None:
    for schema, handler, emoji in (
        (POST_SCHEMA, _handle_post, "✍️"),
        (REPLY_SCHEMA, _handle_reply, "↩️"),
        (LIKE_SCHEMA, _handle_like, "❤️"),
        (FOLLOW_SCHEMA, _handle_follow, "➕"),
    ):
        ctx.register_tool(
            name=schema["name"],
            toolset="xactions_publisher",
            schema=schema,
            handler=handler,
            check_fn=_check_available,
            emoji=emoji,
        )
    ctx.register_hook("pre_tool_call", _approval_hook)
