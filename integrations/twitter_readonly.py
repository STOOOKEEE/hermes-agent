#!/usr/bin/env python3
"""Façade en lecture seule pour twitter-cli.

Elle charge les cookies conservés par Agent Reach sans les imprimer et refuse
les sous-commandes mutantes. La publication est réservée au plugin Hermes
``xactions-publisher``, qui impose une approbation humaine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


SOCIAL_ROOT = Path.home() / ".local" / "share" / "hermes-social"
VENV_ROOT = SOCIAL_ROOT / "agent-reach-venv"
VENV_PYTHON = VENV_ROOT / "bin" / "python"
TWITTER_REAL = VENV_ROOT / "bin" / "twitter"
CONFIG_PATH = Path.home() / ".agent-reach" / "config.yaml"

READ_ONLY_COMMANDS = {
    "article",
    "bookmarks",
    "feed",
    "followers",
    "following",
    "likes",
    "list",
    "search",
    "show",
    "status",
    "tweet",
    "user",
    "user-posts",
}
GLOBAL_FLAGS = {"-v", "--verbose"}
INFORMATION_FLAGS = {"-h", "--help", "--version"}


def _reexec_in_private_venv() -> None:
    if os.environ.get("HERMES_TWITTER_READONLY_REEXEC") == "1":
        return
    if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        env = os.environ.copy()
        env["HERMES_TWITTER_READONLY_REEXEC"] = "1"
        os.execve(
            str(VENV_PYTHON),
            [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            env,
        )


def _command(args: list[str]) -> str | None:
    remaining = list(args)
    while remaining and remaining[0] in GLOBAL_FLAGS:
        remaining.pop(0)
    if not remaining:
        return None
    return remaining[0]


def _load_private_environment() -> dict[str, str]:
    import yaml

    env = os.environ.copy()
    if not CONFIG_PATH.is_file():
        return env

    mode = CONFIG_PATH.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(
            f"{CONFIG_PATH} doit être privé (chmod 600) avant utilisation"
        )

    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration Agent Reach invalide : {CONFIG_PATH}")

    auth_token = str(raw.get("twitter_auth_token") or "").strip()
    ct0 = str(raw.get("twitter_ct0") or "").strip()
    if auth_token:
        env["TWITTER_AUTH_TOKEN"] = auth_token
    if ct0:
        env["TWITTER_CT0"] = ct0

    proxy = str(raw.get("proxy") or "").strip()
    if proxy and "TWITTER_PROXY" not in env:
        env["TWITTER_PROXY"] = proxy
    return env


def main(argv: list[str] | None = None) -> int:
    _reexec_in_private_venv()
    args = list(sys.argv[1:] if argv is None else argv)
    command = _command(args)

    if command in INFORMATION_FLAGS or command is None:
        pass
    elif command not in READ_ONLY_COMMANDS:
        print(
            "Commande twitter-cli refusée : cette façade est strictement en lecture seule. "
            "Utiliser l’outil Hermes xactions_post_tweet pour publier avec approbation.",
            file=sys.stderr,
        )
        return 77

    if not TWITTER_REAL.is_file():
        print(f"twitter-cli privé introuvable : {TWITTER_REAL}", file=sys.stderr)
        return 127

    try:
        env = _load_private_environment()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 78

    os.execve(str(TWITTER_REAL), [str(TWITTER_REAL), *args], env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
