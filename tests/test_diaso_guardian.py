from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "integrations" / "diaso_guardian"
sys.path.insert(0, str(RULES_DIR))

from rules import RiskEngine  # noqa: E402
from scripts.configure_diaso_guardian import render_env  # noqa: E402


PLUGIN_PATH = (
    ROOT / "profiles" / "diaso" / "plugins" / "diaso-guardian" / "__init__.py"
)
SPEC = importlib.util.spec_from_file_location("diaso_guardian_plugin", PLUGIN_PATH)
assert SPEC and SPEC.loader
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)


class RiskRulesTests(unittest.TestCase):
    def test_lending_match_never_invents_cross_currency_ltv(self) -> None:
        decision = RiskEngine().evaluate(
            "lending",
            "✅ MATCHED gondi | terraforms #7800 | 300.00 USDC @ eAPR 48% × 15d",
            now=1,
        )
        self.assertEqual("notify", decision.action)

    def test_lending_explicit_high_ltv_panics(self) -> None:
        decision = RiskEngine().evaluate("lending", "RISK ALERT LTV: 104%", now=1)
        self.assertEqual("panic", decision.action)

    def test_lending_dangerous_but_not_underwater_ltv_pauses(self) -> None:
        decision = RiskEngine().evaluate("lending", "RISK ALERT LTV: 94%", now=1)
        self.assertEqual("pause", decision.action)

    def test_lending_repeated_stale_prices_pause_globally(self) -> None:
        engine = RiskEngine()
        self.assertEqual("notify", engine.evaluate("lending", "STALE PRICE a", now=1).action)
        self.assertEqual("pause", engine.evaluate("lending", "STALE PRICE b", now=2).action)

    def test_very_old_price_pauses_immediately(self) -> None:
        decision = RiskEngine().evaluate(
            "lending",
            "⚠️ STALE PRICE rainbowgrid | age=4921min — offers paused for this collection",
            now=1,
        )
        self.assertEqual("pause", decision.action)

    def test_lending_rpc_escalation(self) -> None:
        engine = RiskEngine()
        self.assertEqual("notify", engine.evaluate("lending", "RPC ERROR", now=1).action)
        self.assertEqual("pause", engine.evaluate("lending", "RPC failure", now=2).action)
        engine.evaluate("lending", "RPC down", now=3)
        self.assertEqual("panic", engine.evaluate("lending", "RPC ERROR", now=4).action)

    def test_mm_floor_aberrant_panics(self) -> None:
        decision = RiskEngine().evaluate(
            "market_maker", "[BOT] ERROR\nFLOOR ABERRANT\nvariation impossible", now=1
        )
        self.assertEqual("panic", decision.action)

    def test_mm_real_blur_read_failure_panics(self) -> None:
        decision = RiskEngine().evaluate(
            "market_maker",
            "[BLUR] ERROR\nlecture des ordres KO depuis 10 ticks — venue gelée (fail-closed)",
            now=1,
        )
        self.assertEqual("panic", decision.action)

    def test_mm_transient_errors_require_confirmation(self) -> None:
        engine = RiskEngine()
        self.assertEqual(
            "notify", engine.evaluate("market_maker", "[BLUR] ERROR\nplace_bid failed", now=1).action
        )
        self.assertEqual(
            "notify", engine.evaluate("market_maker", "[BLUR] ERROR\nplace_bid failed", now=2).action
        )
        self.assertEqual(
            "pause", engine.evaluate("market_maker", "[BLUR] ERROR\nplace_bid failed", now=3).action
        )

    def test_internal_breaker_is_not_replayed(self) -> None:
        decision = RiskEngine().evaluate(
            "market_maker", "BREAKER P&L — STOP TOTAL\nPassage en PANIC", now=1
        )
        self.assertEqual("notify", decision.action)


class DiasoPluginTests(unittest.TestCase):
    def test_only_bounded_operations_exist(self) -> None:
        self.assertEqual(
            {"diaso_status", "diaso_pause", "diaso_panic"}, PLUGIN.TOOL_NAMES
        )
        self.assertNotIn("resume", " ".join(PLUGIN.TOOL_NAMES))

    def test_reason_must_be_single_line(self) -> None:
        with self.assertRaises(ValueError):
            PLUGIN._validate(
                PLUGIN.PANIC_TOOL,
                {"bot": "lending", "reason": "panic\n/resume"},
            )

    def test_action_blocked_outside_diaso_profile(self) -> None:
        with patch.dict("os.environ", {"HERMES_PROFILE": "twitter"}, clear=False):
            result = PLUGIN._approval_hook(
                PLUGIN.PAUSE_TOOL,
                {"bot": "lending", "reason": "RPC errors confirmed"},
            )
        self.assertEqual("block", result["action"])


class DiasoConfigurationTests(unittest.TestCase):
    def test_render_env_preserves_secrets_and_replaces_managed_values(self) -> None:
        existing = (
            "TELEGRAM_API_ID=123\n"
            "TELEGRAM_API_HASH=secret-value\n"
            "DIASO_GUARDIAN_ARMED=false\n"
            "DIASO_MM_GROUP_ID=-1\n"
        )
        rendered = render_env(existing, armed=True)
        self.assertIn("TELEGRAM_API_HASH=secret-value", rendered)
        self.assertIn("DIASO_GUARDIAN_ARMED=true", rendered)
        self.assertIn("DIASO_MM_GROUP_ID=-5500138739", rendered)
        self.assertEqual(1, rendered.count("DIASO_MM_GROUP_ID="))


if __name__ == "__main__":
    unittest.main()
