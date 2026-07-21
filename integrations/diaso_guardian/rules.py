"""Règles déterministes du garde-fou Diaso.

Ce module n'effectue aucune I/O. Il transforme uniquement les messages des bots
Telegram en décisions défensives bornées. Les seules mutations possibles en aval
sont ``pause`` et ``panic`` ; une reprise reste toujours manuelle.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal


BotName = Literal["lending", "market_maker"]
Action = Literal["none", "notify", "pause", "panic"]


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    severity: Literal["info", "warning", "critical"] = "info"


NO_ACTION = Decision("none", "message sans signal de risque")

_LTV_RE = re.compile(r"\bLTV\s*(?:[:=]|is)?\s*(\d{1,3}(?:[.,]\d+)?)\s*%", re.I)
_AGE_MIN_RE = re.compile(r"\bage\s*=\s*(\d+)\s*min\b", re.I)
_LOAN_EQ_RE = re.compile(
    r"\bloanEq\s*=\s*(\d+(?:[.,]\d+)?)\s*ETH\b",
    re.I,
)
_EXIT_PRICE_RE = re.compile(
    r"\bexit\s*=\s*(\d+(?:[.,]\d+)?)\s*ETH\b",
    re.I,
)
_RISK_SNAPSHOT_RE = re.compile(r"\bRISK\s*\|", re.I)
_COMMAND_RESPONSE_RE = re.compile(
    r"^\s*(?:⏸|🛑).*\b(?:PAUSED|PAUSE|PANIC)\b|"
    r"^\s*(?:Déjà|Already)\s+.*\b(?:paused|running)\b|"
    r"^\s*🟢\s+Already\s+running\b|^\s*<b>Commandes</b>",
    re.I | re.S,
)
_LENDING_MATCH_RE = re.compile(r"\b(?:MATCHED|RENEGOTIATED|REFI)\b", re.I)
_LENDING_STALE_RE = re.compile(r"\bSTALE\s+PRICE\b|\bPRICE\s+STALE\b", re.I)
_LENDING_RPC_RE = re.compile(
    r"\bRPC(?:[\s_-]+(?:ERROR|FAIL(?:ED|URE)?|DOWN|UNAVAILABLE))\b|"
    r"\b(?:ERROR|FAIL(?:ED|URE)?)\b[^\n]{0,80}\bRPC\b",
    re.I,
)
_LENDING_HARD_RE = re.compile(
    r"\b(?:CRITICAL|FATAL)\b|"
    r"\b(?:PRICER|PRICE|FLOOR|ORACLE)\b[^\n]{0,100}"
    r"\b(?:ABERRANT|CORRUPT|WRONG|INVALID|IMPOSSIBLE)\b|"
    r"\b(?:OVERPRICED?|UNDERPRICED?)\s+OFFERS?\b|"
    r"\bDATABASE\b[^\n]{0,80}\b(?:DOWN|UNREACHABLE|CORRUPT)\b",
    re.I,
)

_MM_ALREADY_HARD_RE = re.compile(
    r"BREAKER\s+P&L\s*[—-]\s*STOP\s+TOTAL|"
    r"WATCHDOG[^\n]*(?:PANIC|pause hard)|"
    r"passage en PANIC",
    re.I,
)
_MM_IMMEDIATE_PANIC_RE = re.compile(
    r"FLOOR\s+ABERRANT|"
    r"D[ÉE]SYNC\s+INVENTAIRE\s*/\s*HOLDINGS|"
    r"DRIFT\s+INVENTAIRE|"
    r"Circuit\s+breaker\s+tripped|"
    r"lectures?\s+(?:(?:des?|de)\s+)?(?:march[ée]/ordres|ordres)[^\n]*KO|"
    r"PANIC:\s*invalidation[^\n]*[ée]chou[ée]e",
    re.I,
)
_MM_TRANSIENT_ERROR_RE = re.compile(
    r"\[(?:BLUR|OS|OPENSEA|GONDI)\]\s+ERROR|"
    r"\b(?:place_bid|place_listing|cancel(?:_bid|_listing)?)\b[^\n]*"
    r"\b(?:fail|error|[ée]chou)",
    re.I,
)
_MM_NOTICE_RE = re.compile(r"\[BOT\]\s+NOTICE|INVENTAIRE NON LIST[ÉE]", re.I)
_MM_FILL_RE = re.compile(r"\bFILL\s+(?:BOUGHT|SOLD)\b|[✅🟢]\s*VENTE\b", re.I)
_MM_TIME_STOP_RE = re.compile(r"TIME-STOP\s+INVENTAIRE", re.I)


class RiskEngine:
    """Moteur à mémoire courte pour confirmer les pannes répétées."""

    def __init__(self) -> None:
        self._signals: dict[tuple[BotName, str], deque[float]] = defaultdict(deque)

    def _count(self, bot: BotName, kind: str, now: float, window: float) -> int:
        values = self._signals[(bot, kind)]
        values.append(now)
        while values and now - values[0] > window:
            values.popleft()
        return len(values)

    def evaluate(self, bot: BotName, text: str, *, now: float | None = None) -> Decision:
        timestamp = time.monotonic() if now is None else now
        normalized = text.strip()
        if not normalized or _COMMAND_RESPONSE_RE.search(normalized):
            return NO_ACTION
        if bot == "lending":
            return self._lending(normalized, timestamp)
        if bot == "market_maker":
            return self._market_maker(normalized, timestamp)
        return NO_ACTION

    def _lending(self, text: str, now: float) -> Decision:
        ltv_match = _LTV_RE.search(text)
        ltv = float(ltv_match.group(1).replace(",", ".")) if ltv_match else None
        verified_match_reason: str | None = None
        if _LENDING_MATCH_RE.search(text):
            loan_match = _LOAN_EQ_RE.search(text)
            exit_match = _EXIT_PRICE_RE.search(text)
            age_match = _AGE_MIN_RE.search(text)
            has_snapshot = _RISK_SNAPSHOT_RE.search(text) is not None

            if not has_snapshot:
                return Decision(
                    "notify",
                    "audit incomplet : le message de match ne contient pas de snapshot RISK",
                    "warning",
                )
            if ltv is None or loan_match is None or exit_match is None or age_match is None:
                return Decision(
                    "pause",
                    "snapshot RISK incomplet ou illisible sur un nouveau loan",
                    "critical",
                )

            loan_eth = float(loan_match.group(1).replace(",", "."))
            exit_eth = float(exit_match.group(1).replace(",", "."))
            age_minutes = int(age_match.group(1))
            if loan_eth <= 0 or exit_eth <= 0:
                return Decision(
                    "panic",
                    "montant ou prix de sortie invalide dans le snapshot RISK",
                    "critical",
                )
            recomputed_ltv = 100 * loan_eth / exit_eth
            if abs(recomputed_ltv - ltv) > 1.0:
                return Decision(
                    "panic",
                    "LTV incohérent : "
                    f"annoncé {ltv:.1f} %, recalculé {recomputed_ltv:.1f} %",
                    "critical",
                )
            if age_minutes >= 60:
                return Decision(
                    "pause",
                    f"loan matché avec un prix âgé de {age_minutes} min",
                    "critical",
                )
            verified_match_reason = (
                f"loan vérifié : LTV {ltv:.1f} %, prix âgé de {age_minutes} min"
            )

        if ltv_match:
            assert ltv is not None
            if ltv >= 100:
                return Decision(
                    "panic",
                    f"LTV sous l'eau ({ltv:.1f} %) : arrêt et annulation des offres",
                    "critical",
                )
            if ltv >= 90:
                return Decision(
                    "pause",
                    f"LTV explicitement dangereux ({ltv:.1f} %)",
                    "critical",
                )
            if ltv >= 80:
                return Decision("notify", f"LTV élevé observé ({ltv:.1f} %)", "warning")

        if _LENDING_HARD_RE.search(text):
            return Decision(
                "panic",
                "prix/pricer ou infrastructure critique explicitement incohérent",
                "critical",
            )

        if _LENDING_STALE_RE.search(text):
            age_match = _AGE_MIN_RE.search(text)
            age_minutes = int(age_match.group(1)) if age_match else None
            if age_minutes is not None and age_minutes >= 60:
                return Decision(
                    "pause",
                    f"prix périmé depuis {age_minutes} min",
                    "critical",
                )
            count = self._count("lending", "stale_price", now, 6 * 60 * 60)
            if count >= 2:
                return Decision(
                    "pause",
                    f"prix périmés répétés ({count} alertes en 6 h)",
                    "critical",
                )
            return Decision(
                "notify",
                f"prix périmé isolé ({count}/2 avant pause globale)",
                "warning",
            )

        if _LENDING_RPC_RE.search(text):
            count = self._count("lending", "rpc", now, 5 * 60)
            if count >= 4:
                return Decision(
                    "panic",
                    f"panne RPC persistante ({count} erreurs en 5 min)",
                    "critical",
                )
            if count >= 2:
                return Decision(
                    "pause",
                    f"panne RPC confirmée ({count} erreurs en 5 min)",
                    "critical",
                )
            return Decision("notify", "première erreur RPC, en attente de confirmation", "warning")

        if verified_match_reason is not None:
            return Decision(
                "notify",
                verified_match_reason,
                "info",
            )
        return NO_ACTION

    def _market_maker(self, text: str, now: float) -> Decision:
        if _MM_ALREADY_HARD_RE.search(text):
            return Decision(
                "notify",
                "le bot annonce avoir déjà activé sa pause hard interne",
                "critical",
            )
        if _MM_IMMEDIATE_PANIC_RE.search(text):
            return Decision(
                "panic",
                "état de marché, d'inventaire ou d'ordres non fiable",
                "critical",
            )
        if _MM_TRANSIENT_ERROR_RE.search(text):
            count = self._count("market_maker", "venue_error", now, 10 * 60)
            if count >= 6:
                return Decision(
                    "panic",
                    f"erreurs d'exécution persistantes ({count} en 10 min)",
                    "critical",
                )
            if count >= 3:
                return Decision(
                    "pause",
                    f"erreurs d'exécution répétées ({count} en 10 min)",
                    "critical",
                )
            return Decision(
                "notify",
                f"erreur d'exécution isolée ({count}/3 avant pause)",
                "warning",
            )
        if _MM_NOTICE_RE.search(text):
            return Decision("notify", "notice de gestion d'inventaire", "warning")
        if _MM_TIME_STOP_RE.search(text):
            return Decision("notify", "liquidation time-stop prévue par la stratégie", "warning")
        if _MM_FILL_RE.search(text):
            return Decision("notify", "activité market making observée", "info")
        return NO_ACTION
