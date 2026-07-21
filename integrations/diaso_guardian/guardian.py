#!/usr/bin/env python3
"""Pont défensif Telegram -> Discord pour les bots Diaso.

Le processus possède l'unique session Telethon. Il écoute les deux groupes de
production, applique les règles déterministes de :mod:`rules`, expose un socket Unix
local aux outils Hermes et journalise chaque décision. Il n'implémente volontairement
aucune commande de reprise ou d'augmentation d'exposition.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import stat
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rules import BotName, Decision, RiskEngine


BASE = Path(__file__).resolve().parent
LISTENER_HOME = Path.home() / "telethon-listener"
STATE_HOME = Path.home() / ".local" / "state" / "diaso-guardian"
LOG_PATH = STATE_HOME / "decisions.jsonl"
SOCKET_PATH = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "diaso-guardian.sock"
MAX_REQUEST_BYTES = 8_192
MAX_REASON_LENGTH = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diaso-guardian")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        os.environ.setdefault(key, value.strip().strip("\"'"))


_load_env_file(LISTENER_HOME / ".env")
_load_env_file(Path.home() / ".hermes" / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"variable requise absente: {name}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BotConfig:
    chat_id: int
    username: str


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_path: Path
    discord_channel_id: str
    discord_token: str
    armed: bool
    bots: dict[BotName, BotConfig]

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_id=int(_required("TELEGRAM_API_ID")),
            api_hash=_required("TELEGRAM_API_HASH"),
            session_path=LISTENER_HOME / "hermes_session",
            discord_channel_id=_required("DIASO_DISCORD_CHANNEL_ID"),
            discord_token=os.getenv("DISCORD_BOT_TOKEN", "").strip(),
            armed=_env_bool("DIASO_GUARDIAN_ARMED"),
            bots={
                "lending": BotConfig(
                    int(_required("DIASO_LENDING_GROUP_ID")),
                    _required("DIASO_LENDING_BOT_USERNAME").lstrip("@").lower(),
                ),
                "market_maker": BotConfig(
                    int(_required("DIASO_MM_GROUP_ID")),
                    _required("DIASO_MM_BOT_USERNAME").lstrip("@").lower(),
                ),
            },
        )


def _safe_excerpt(text: str, limit: int = 500) -> str:
    excerpt = re.sub(r"0x[a-fA-F0-9]{40}", lambda m: f"{m.group(0)[:8]}…{m.group(0)[-4:]}", text)
    excerpt = re.sub(r"\b\d{10}:[-\w]{20,}\b", "[telegram-token-redacted]", excerpt)
    return excerpt[:limit]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_audit(payload: dict[str, Any]) -> None:
    STATE_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": _utc_now(), **payload}, ensure_ascii=False) + "\n")
    LOG_PATH.chmod(0o600)


class DiscordNotifier:
    def __init__(self, token: str, channel_id: str) -> None:
        self.token = token
        self.channel_id = channel_id

    async def send(self, content: str) -> None:
        if not self.token:
            log.warning("DISCORD_BOT_TOKEN absent: notification Discord ignorée")
            return
        await asyncio.to_thread(self._send_sync, content[:1900])

    def _send_sync(self, content: str) -> None:
        request = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
            data=json.dumps({"content": content}).encode("utf-8"),
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "DiasoGuardian/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status >= 300:
                    raise RuntimeError(f"Discord HTTP {response.status}")
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            log.error("notification Discord échouée: %s", exc)


class Guardian:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.rules = RiskEngine()
        self.notifier = DiscordNotifier(config.discord_token, config.discord_channel_id)
        self.client: Any = None
        self.server: asyncio.AbstractServer | None = None
        self._bot_locks = {name: asyncio.Lock() for name in config.bots}
        self._waiters: dict[BotName, list[tuple[re.Pattern[str], asyncio.Future[str]]]] = {
            name: [] for name in config.bots
        }
        self._last_auto_action: dict[tuple[BotName, str], float] = {}
        self._last_notification: dict[str, float] = {}

    def _bot_for_event(self, chat_id: int, username: str) -> BotName | None:
        normalized = username.lstrip("@").lower()
        for name, bot in self.config.bots.items():
            if chat_id == bot.chat_id and normalized == bot.username:
                return name
        return None

    def _feed_waiters(self, bot: BotName, text: str) -> None:
        remaining: list[tuple[re.Pattern[str], asyncio.Future[str]]] = []
        for pattern, future in self._waiters[bot]:
            if future.done():
                continue
            if pattern.search(text):
                future.set_result(text)
            else:
                remaining.append((pattern, future))
        self._waiters[bot] = remaining

    async def _notify_decision(
        self,
        bot: BotName,
        decision: Decision,
        source: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        label = "NFT lending" if bot == "lending" else "NFT market making"
        mode = "ARMÉ" if self.config.armed else "SIMULATION"
        lines = [
            f"🛡️ **Diaso Guardian — {label}**",
            f"Décision : **{decision.action.upper()}** · mode {mode}",
            f"Raison : {decision.reason}",
            f"Signal : `{_safe_excerpt(source, 450)}`",
        ]
        if result is not None:
            lines.append(f"Résultat : `{_safe_excerpt(json.dumps(result, ensure_ascii=False), 450)}`")
        await self.notifier.send("\n".join(lines))

    async def _on_bot_message(self, bot: BotName, text: str, message_id: int) -> None:
        self._feed_waiters(bot, text)
        decision = self.rules.evaluate(bot, text)
        if decision.action == "none":
            return

        audit = {
            "kind": "signal",
            "bot": bot,
            "message_id": message_id,
            "decision": asdict(decision),
            "armed": self.config.armed,
            "excerpt": _safe_excerpt(text),
        }
        _append_audit(audit)

        if decision.action == "notify":
            digest = hashlib.sha256(
                f"{bot}\0{decision.reason}\0{_safe_excerpt(text)}".encode("utf-8")
            ).hexdigest()
            now = time.monotonic()
            if now - self._last_notification.get(digest, 0.0) < 60 * 60:
                return
            self._last_notification[digest] = now
            await self._notify_decision(bot, decision, text)
            return
        if not self.config.armed:
            await self._notify_decision(bot, decision, text)
            return

        cooldown = 30 * 60 if decision.action == "panic" else 10 * 60
        key = (bot, decision.action)
        now = time.monotonic()
        if now - self._last_auto_action.get(key, 0.0) < cooldown:
            log.warning("action automatique %s/%s sous cooldown", bot, decision.action)
            return
        self._last_auto_action[key] = now
        result = await self.execute(bot, decision.action, decision.reason, automatic=True)
        await self._notify_decision(bot, decision, text, result=result)

    async def _send_and_wait(
        self,
        bot: BotName,
        command: str,
        expected: str,
        *,
        timeout: float = 25,
    ) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        waiter = (re.compile(expected, re.I | re.S), future)
        self._waiters[bot].append(waiter)
        try:
            await self.client.send_message(self.config.bots[bot].chat_id, command)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._waiters[bot] = [entry for entry in self._waiters[bot] if entry[1] is not future]

    async def _status(self, bot: BotName) -> dict[str, Any]:
        try:
            response = await self._send_and_wait(
                bot,
                "/status",
                r"STATUS|PAUSED|RUNNING|wallet|active loans|orders|PnL|inventaire|deployed",
            )
            return {
                "ok": True,
                "bot": bot,
                "paused": bool(re.search(r"\bPAUSED\b|\bPANIC\b", response, re.I)),
                "status": _safe_excerpt(response, 1_500),
            }
        except asyncio.TimeoutError:
            return {"ok": False, "bot": bot, "error": "aucune réponse au /status en 25 s"}

    async def execute(
        self,
        bot: BotName,
        operation: str,
        reason: str,
        *,
        automatic: bool,
    ) -> dict[str, Any]:
        if operation not in {"status", "pause", "panic"}:
            return {"ok": False, "error": "opération interdite"}
        if bot not in self.config.bots:
            return {"ok": False, "error": "bot inconnu"}
        clean_reason = re.sub(r"[\r\n]+", " ", reason).strip()[:MAX_REASON_LENGTH]

        async with self._bot_locks[bot]:
            if operation == "status":
                result = await self._status(bot)
            elif not self.config.armed:
                result = {
                    "ok": False,
                    "bot": bot,
                    "operation": operation,
                    "error": "guardian en simulation; aucune commande envoyée",
                }
            else:
                responses: list[str] = []
                try:
                    pause_response = await self._send_and_wait(
                        bot,
                        "/pause",
                        r"PAUSED|Already paused|pause|offers? blocked|achats? coup[ée]s?",
                    )
                    responses.append(_safe_excerpt(pause_response))
                    if operation == "panic":
                        panic_response = await self._send_and_wait(
                            bot,
                            "/panic confirm",
                            r"PANIC|cancel|invalid|hide|annul|hard",
                            timeout=45,
                        )
                        responses.append(_safe_excerpt(panic_response))
                    status = await self._status(bot)
                    failed_ack = any(
                        re.search(r"❌|[ÉE]CHOU[ÉE]|FAILED|TOUJOURS ACTIF", response, re.I)
                        for response in responses
                    )
                    verified_paused = bool(status.get("ok") and status.get("paused"))
                    result = {
                        "ok": not failed_ack and verified_paused,
                        "bot": bot,
                        "operation": operation,
                        "responses": responses,
                        "verification": status,
                    }
                    if failed_ack:
                        result["error"] = "au moins une étape du kill-switch a échoué"
                    elif not verified_paused:
                        result["error"] = "l'état PAUSED/PANIC n'a pas été vérifié"
                except asyncio.TimeoutError:
                    result = {
                        "ok": False,
                        "bot": bot,
                        "operation": operation,
                        "error": "commande envoyée mais accusé de réception non vérifié",
                    }

            _append_audit(
                {
                    "kind": "action",
                    "bot": bot,
                    "operation": operation,
                    "automatic": automatic,
                    "reason": clean_reason,
                    "result": result,
                }
            )
            return result

    async def _handle_socket(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("requête vide ou trop longue")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("requête invalide")
            operation = str(payload.get("operation") or "")
            bot = str(payload.get("bot") or "")
            reason = str(payload.get("reason") or "action demandée depuis Discord")

            if operation == "status" and bot == "all":
                result = {
                    "ok": True,
                    "armed": self.config.armed,
                    "bots": [
                        await self.execute(name, "status", reason, automatic=False)
                        for name in ("lending", "market_maker")
                    ],
                }
            elif bot in self.config.bots:
                result = await self.execute(bot, operation, reason, automatic=False)  # type: ignore[arg-type]
            else:
                result = {"ok": False, "error": "bot invalide"}
        except (ValueError, json.JSONDecodeError, asyncio.TimeoutError) as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception as exc:  # garde le service disponible sans exposer de secret
            log.exception("requête socket échouée")
            result = {"ok": False, "error": type(exc).__name__}
        writer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def start(self) -> None:
        from telethon import TelegramClient, events

        STATE_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        session_file = self.config.session_path.with_suffix(".session")
        if session_file.exists() and stat.S_IMODE(session_file.stat().st_mode) & 0o077:
            raise RuntimeError(f"permissions trop ouvertes sur {session_file}")

        self.client = TelegramClient(
            str(self.config.session_path), self.config.api_id, self.config.api_hash
        )

        chat_ids = [bot.chat_id for bot in self.config.bots.values()]

        @self.client.on(events.NewMessage(chats=chat_ids))
        async def handler(event: Any) -> None:
            sender = await event.get_sender()
            username = str(getattr(sender, "username", "") or "")
            bot = self._bot_for_event(int(event.chat_id), username)
            if bot is None:
                return
            text = str(event.message.text or "")
            await self._on_bot_message(bot, text, int(event.message.id))

        await self.client.start()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        self.server = await asyncio.start_unix_server(self._handle_socket, path=SOCKET_PATH)
        SOCKET_PATH.chmod(0o600)
        log.info(
            "Diaso Guardian démarré: bots=%s, armé=%s, socket=%s",
            ",".join(self.config.bots),
            self.config.armed,
            SOCKET_PATH,
        )
        await self.client.run_until_disconnected()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.client is not None:
            await self.client.disconnect()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


async def main() -> None:
    guardian = Guardian(Config.from_env())
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(guardian.start())
    stop_task = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        await task
    else:
        await guardian.close()
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
