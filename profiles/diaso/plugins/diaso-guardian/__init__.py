"""Outils structurés du profil Discord Diaso."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any


PAUSE_TOOL = "diaso_pause"
PANIC_TOOL = "diaso_panic"
TOOL_NAMES = {PAUSE_TOOL, PANIC_TOOL}
ALLOWED_BOTS = {"lending", "market_maker"}
MAX_REASON_LENGTH = 300
SOCKET_TIMEOUT = 90

PAUSE_SCHEMA = {
    "name": PAUSE_TOOL,
    "description": (
        "Bloque immédiatement la création de nouvelle exposition sur un bot Diaso. "
        "Action défensive et sticky ; aucune reprise n'est possible avec cet outil."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bot": {"type": "string", "enum": ["lending", "market_maker"]},
            "reason": {
                "type": "string",
                "minLength": 3,
                "maxLength": MAX_REASON_LENGTH,
                "description": "Motif factuel et vérifiable de l'arrêt.",
            },
        },
        "required": ["bot", "reason"],
        "additionalProperties": False,
    },
}

PANIC_SCHEMA = {
    "name": PANIC_TOOL,
    "description": (
        "Met en pause puis annule/invalide les offres actives d'un bot Diaso. "
        "Réservé aux signaux critiques ; peut occasionner des frais de gas. "
        "N'offre aucune commande de reprise."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bot": {"type": "string", "enum": ["lending", "market_maker"]},
            "reason": {
                "type": "string",
                "minLength": 3,
                "maxLength": MAX_REASON_LENGTH,
                "description": "Signal critique exact justifiant l'annulation.",
            },
        },
        "required": ["bot", "reason"],
        "additionalProperties": False,
    },
}


def _active_profile_is_diaso() -> bool:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).name == "diaso"
    except ImportError:
        return os.getenv("HERMES_PROFILE", "").strip().lower() == "diaso"


def _socket_path() -> Path:
    runtime = os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "diaso-guardian.sock"


def _validate(tool_name: str, args: Any) -> dict[str, str]:
    if not isinstance(args, dict):
        raise ValueError("les arguments doivent être un objet")
    bot = str(args.get("bot") or "")
    if bot not in ALLOWED_BOTS:
        raise ValueError("bot invalide")
    reason = str(args.get("reason") or "").strip()
    if not 3 <= len(reason) <= MAX_REASON_LENGTH or "\n" in reason or "\r" in reason:
        raise ValueError("reason doit contenir 3 à 300 caractères sur une ligne")
    return {"bot": bot, "reason": reason}


def _check_available() -> bool:
    path = _socket_path()
    return _active_profile_is_diaso() and path.exists() and path.is_socket()


def _request(operation: str, bot: str, reason: str) -> str:
    path = _socket_path()
    if not _active_profile_is_diaso():
        return json.dumps(
            {"ok": False, "error": "outil Diaso indisponible hors du profil diaso"},
            ensure_ascii=False,
        )
    if not path.exists() or not path.is_socket():
        return json.dumps(
            {"ok": False, "error": "service diaso-guardian indisponible"},
            ensure_ascii=False,
        )
    payload = json.dumps(
        {"operation": operation, "bot": bot, "reason": reason}, ensure_ascii=False
    ).encode("utf-8") + b"\n"
    if len(payload) > 8_192:
        return json.dumps({"ok": False, "error": "requête trop longue"}, ensure_ascii=False)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(SOCKET_TIMEOUT)
            client.connect(str(path))
            client.sendall(payload)
            chunks: list[bytes] = []
            size = 0
            while size < 128_000:
                chunk = client.recv(min(16_384, 128_000 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError):
        return json.dumps(
            {"ok": False, "error": "diaso-guardian n'a pas répondu"},
            ensure_ascii=False,
        )

    raw = b"".join(chunks).split(b"\n", 1)[0]
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        response = {"ok": False, "error": "réponse invalide du guardian"}
    return json.dumps(response, ensure_ascii=False)


def _handle(tool_name: str, args: Any) -> str:
    try:
        clean = _validate(tool_name, args)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    operation = {
        PAUSE_TOOL: "pause",
        PANIC_TOOL: "panic",
    }[tool_name]
    return _request(operation, clean["bot"], clean["reason"])


def _approval_hook(tool_name: str = "", args: Any = None, **_: Any) -> dict[str, str] | None:
    if tool_name not in TOOL_NAMES:
        return None
    try:
        _validate(tool_name, args)
    except ValueError as exc:
        return {"action": "block", "message": f"Action Diaso refusée : {exc}"}
    if not _active_profile_is_diaso():
        return {"action": "block", "message": "Action Diaso refusée hors du profil diaso"}
    # Pause et panic réduisent uniquement l'exposition. L'utilisateur a autorisé
    # explicitement leur emploi sans présence humaine ; aucune seconde approbation.
    return None


def register(ctx) -> None:
    for schema, emoji in (
        (PAUSE_SCHEMA, "⏸️"),
        (PANIC_SCHEMA, "🛑"),
    ):
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="diaso_guardian",
            schema=schema,
            handler=lambda args, _name=name, **_: _handle(_name, args),
            check_fn=_check_available,
            emoji=emoji,
        )
    ctx.register_hook("pre_tool_call", _approval_hook)
