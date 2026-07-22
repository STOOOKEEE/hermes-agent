#!/usr/bin/env python3
"""Collecte un contexte ArtBytes quotidien sans modifier le curseur du scanner.

Le script est injecté dans deux jobs Hermes pilotés par un agent : le brief du matin
et le récapitulatif du soir. Il lit les salons ArtBytes en lecture seule, sélectionne
un échantillon représentatif des dernières 36 heures et joint des extraits des
mémoires de veille. Aucun message Discord n'est envoyé par ce script.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TOKEN_FILE = Path("~/.hermes/secrets/discord_token").expanduser()
MEMORY_ROOT = Path("~/.hermes/memories/artbytes").expanduser()
LOOKBACK_HOURS = 36
PER_CHANNEL_LIMIT = 100
MAX_SELECTED_MESSAGES = 300
MAX_CONTENT_CHARS = 500
MAX_MEMORY_CHARS = 30_000
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CHANNELS = {
    "announcement": "1394476834229452861",
    "alpha": "1394477401148358850",
    "general-chat": "1394477444093837442",
    "raid-it": "1395768486881525770",
    "dracko": "1435221163050795018",
    "giveaway": "1423410333883568180",
}

URL_RE = re.compile(r"https?://[^\s<>()]+")
PRIORITY_RE = re.compile(
    r"\b(mint|wl|allowlist|gtd|fcfs|checker|wallet|supply|price|prix|chain|"
    r"contract|contrat|raid|burn|reveal|secondary|floor|sweep)\b",
    re.IGNORECASE,
)


class ContextError(RuntimeError):
    """La collecte ne peut pas produire un contexte fiable."""


def snowflake_from_time(moment: datetime) -> str:
    epoch_ms = 1_420_070_400_000
    discord_ms = int(moment.timestamp() * 1000) - epoch_ms
    return str(discord_ms << 22)


def _load_token(path: Path = TOKEN_FILE) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ContextError(f"token Discord inaccessible: {path}") from exc
    if not token:
        raise ContextError(f"token Discord vide: {path}")
    return token


def _api_get(token: str, channel_id: str, after: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"limit": PER_CHANNEL_LIMIT, "after": after})
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages?{query}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": token, "User-Agent": UA, "Accept": "application/json"},
    )
    for _attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            if not isinstance(payload, list):
                raise ContextError("réponse Discord inattendue")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise ContextError(f"Discord HTTP {exc.code} pour {channel_id}") from exc
            retry_after = 2.0
            try:
                retry_after = float(json.load(exc).get("retry_after", retry_after))
            except Exception:
                pass
            time.sleep(min(retry_after + 0.5, 10))
    raise ContextError(f"Discord rate-limit persistant pour {channel_id}")


def _message(channel: str, raw: dict[str, Any]) -> dict[str, Any]:
    content = str(raw.get("content") or "").strip()
    urls = URL_RE.findall(content)
    author = str((raw.get("author") or {}).get("username") or "?")
    return {
        "id": str(raw.get("id") or ""),
        "channel": channel,
        "timestamp": str(raw.get("timestamp") or "")[:19],
        "author": author,
        "content": content[:MAX_CONTENT_CHARS],
        "urls": urls[:8],
        "priority": bool(
            urls
            or PRIORITY_RE.search(content)
            or "krysko" in author.lower()
            or "drack" in author.lower()
        ),
    }


def _sample(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(messages) <= MAX_SELECTED_MESSAGES:
        return messages
    priority = [message for message in messages if message["priority"]]
    regular = [message for message in messages if not message["priority"]]
    if len(priority) >= MAX_SELECTED_MESSAGES:
        return priority[-MAX_SELECTED_MESSAGES:]
    slots = MAX_SELECTED_MESSAGES - len(priority)
    if len(regular) <= slots:
        selected = priority + regular
    else:
        step = len(regular) / slots
        selected = priority + [
            regular[min(int(index * step), len(regular) - 1)] for index in range(slots)
        ]
    deduplicated = {message["id"]: message for message in selected}
    return sorted(
        deduplicated.values(), key=lambda message: (message["timestamp"], message["id"])
    )


def _excerpt(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "[indisponible]"
    if len(text) <= MAX_MEMORY_CHARS:
        return text
    half = MAX_MEMORY_CHARS // 2
    return text[:half] + "\n\n[… contenu intermédiaire tronqué …]\n\n" + text[-half:]


def collect(now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    after = snowflake_from_time(
        current.astimezone(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    )
    token = _load_token()
    messages: list[dict[str, Any]] = []
    errors: list[str] = []
    for name, channel_id in CHANNELS.items():
        try:
            raw_messages = _api_get(token, channel_id, after)
        except ContextError as exc:
            errors.append(f"{name}: {exc}")
            continue
        messages.extend(_message(name, raw) for raw in raw_messages)
        time.sleep(0.4)
    messages.sort(key=lambda message: (message["timestamp"], message["id"]))
    selected = _sample(messages)
    for message in selected:
        message.pop("priority", None)
    return {
        "generated_at": current.astimezone().isoformat(timespec="seconds"),
        "lookback_hours": LOOKBACK_HOURS,
        "messages_seen": len(messages),
        "messages_selected": len(selected),
        "messages_omitted": max(0, len(messages) - len(selected)),
        "errors": errors,
        "messages": selected,
        "watchlist": _excerpt(MEMORY_ROOT / "mints-watchlist.md"),
        "hype_board": _excerpt(MEMORY_ROOT / "hype-board.md"),
        "insides": _excerpt(MEMORY_ROOT / "insides.md"),
    }


def main() -> int:
    try:
        result = collect()
    except ContextError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
