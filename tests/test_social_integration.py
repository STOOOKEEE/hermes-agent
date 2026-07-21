from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = (
    REPO_ROOT
    / "profiles"
    / "twitter"
    / "plugins"
    / "xactions-publisher"
    / "__init__.py"
)
READER_PLUGIN_PATH = (
    REPO_ROOT
    / "profiles"
    / "twitter"
    / "plugins"
    / "agent-reach-reader"
    / "__init__.py"
)
RUNNER_PATH = PLUGIN_PATH.with_name("runner.mjs")
WRAPPER_PATH = REPO_ROOT / "integrations" / "twitter_readonly.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class XActionsPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _load_module("xactions_publisher_test", PLUGIN_PATH)

    def test_approval_contains_exact_text_and_content_scoped_rule(self) -> None:
        text = "Un post précis 🚀"
        with patch.dict(os.environ, {"XACTIONS_EXPECTED_USERNAME": "@stoookeee"}):
            first = self.plugin._approval_hook(self.plugin.TOOL_NAME, {"text": text})
            second = self.plugin._approval_hook(
                self.plugin.TOOL_NAME, {"text": text + " modifié"}
            )

        self.assertEqual("approve", first["action"])
        self.assertIn(text, first["message"])
        self.assertIn("@stoookeee", first["message"])
        self.assertNotEqual(first["rule_key"], second["rule_key"])

    def test_terminal_mutation_is_blocked(self) -> None:
        result = self.plugin._approval_hook(
            "terminal", {"command": "twitter post 'contournement'"}
        )
        self.assertEqual("block", result["action"])
        self.assertIsNone(
            self.plugin._approval_hook("terminal", {"command": "twitter search IA"})
        )

    def test_handler_keeps_cookies_out_of_process_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.yaml"
            config.write_text(
                "twitter_auth_token: top-secret-auth\n"
                "twitter_ct0: top-secret-ct0\n",
                encoding="utf-8",
            )
            config.chmod(0o600)
            xactions = root / "xactions"
            xactions.mkdir()

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {
                        "success": True,
                        "account": "@stoookeee",
                        "tweet_id": "42",
                        "url": "https://x.com/stoookeee/status/42",
                    }
                ),
                stderr="",
            )
            with (
                patch.object(self.plugin, "X_CONFIG", config),
                patch.object(self.plugin, "X_ACTIONS_ROOT", xactions),
                patch.object(self.plugin.shutil, "which", return_value="/usr/bin/node"),
                patch.object(self.plugin.subprocess, "run", return_value=completed) as run,
                patch.dict(os.environ, {"XACTIONS_EXPECTED_USERNAME": "stoookeee"}),
            ):
                result = json.loads(self.plugin._handle_post({"text": "Bonjour"}))

            self.assertTrue(result["success"])
            command = run.call_args.args[0]
            self.assertNotIn("top-secret-auth", " ".join(command))
            self.assertNotIn("top-secret-ct0", " ".join(command))
            self.assertEqual("top-secret-auth", run.call_args.kwargs["env"]["XACTIONS_AUTH_TOKEN"])
            self.assertEqual(json.dumps({"text": "Bonjour"}, ensure_ascii=False), run.call_args.kwargs["input"])


class XActionsRunnerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js absent")
    def test_runner_checks_account_and_returns_verified_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module_dir = root / "src" / "scrapers" / "twitter" / "http"
            module_dir.mkdir(parents=True)
            (root / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
            (module_dir / "client.js").write_text(
                "export class TwitterHttpClient { constructor(options) { this.options = options; } }\n",
                encoding="utf-8",
            )
            (module_dir / "auth.js").write_text(
                "export class TwitterAuth { async loginWithCookies() { "
                "return {id:'1', username:'STOOOKEEE', name:'Owner'}; } }\n",
                encoding="utf-8",
            )
            (module_dir / "actions.js").write_text(
                "export async function postTweet(client, text) { "
                "return {rest_id:'123456789'}; }\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "XACTIONS_ROOT": str(root),
                    "XACTIONS_AUTH_TOKEN": "secret-auth",
                    "XACTIONS_CT0": "secret-ct0",
                    "XACTIONS_EXPECTED_USERNAME": "stoookeee",
                }
            )
            completed = subprocess.run(
                [shutil.which("node"), str(RUNNER_PATH)],
                input=json.dumps({"text": "Test contrôlé"}),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

        result = json.loads(completed.stdout)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue(result["success"])
        self.assertEqual("@stoookeee", result["account"])
        self.assertEqual("https://x.com/stoookeee/status/123456789", result["url"])


class AgentReachReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _load_module("agent_reach_reader_test", READER_PLUGIN_PATH)
        self.plugin._account_cache.update(username="", checked_at=0.0)

    def _configured(self, root: Path):
        config = root / "config.yaml"
        config.write_text(
            "twitter_auth_token: top-secret-auth\n"
            "twitter_ct0: top-secret-ct0\n",
            encoding="utf-8",
        )
        config.chmod(0o600)
        twitter = root / "twitter"
        twitter.write_text("#!/bin/sh\n", encoding="utf-8")
        twitter.chmod(0o755)
        return config, twitter

    def test_status_keeps_cookies_out_of_process_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, twitter = self._configured(Path(temporary))
            payload = {
                "ok": True,
                "data": {"user": {"username": "STOOOKEEE"}},
            }
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(payload), stderr=""
            )
            with (
                patch.object(self.plugin, "X_CONFIG", config),
                patch.object(self.plugin, "TWITTER", twitter),
                patch.object(self.plugin.subprocess, "run", return_value=completed) as run,
                patch.dict(os.environ, {"XACTIONS_EXPECTED_USERNAME": "STOOOKEEE"}),
            ):
                result = json.loads(self.plugin._handle_status({}))

        self.assertTrue(result["ok"])
        command = run.call_args.args[0]
        self.assertEqual([str(twitter), "--compact", "status", "--json"], command)
        self.assertNotIn("top-secret-auth", " ".join(command))
        self.assertNotIn("top-secret-ct0", " ".join(command))
        self.assertNotIn("env", run.call_args.kwargs)

    def test_account_mismatch_blocks_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, twitter = self._configured(Path(temporary))
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "data": {"user": {"username": "OTHER_ACCOUNT"}}}
                ),
                stderr="",
            )
            with (
                patch.object(self.plugin, "X_CONFIG", config),
                patch.object(self.plugin, "TWITTER", twitter),
                patch.object(self.plugin.subprocess, "run", return_value=completed) as run,
                patch.dict(os.environ, {"XACTIONS_EXPECTED_USERNAME": "STOOOKEEE"}),
            ):
                result = json.loads(
                    self.plugin._handle_search({"query": "Hermes", "max_results": 3})
                )

        self.assertFalse(result["ok"])
        self.assertIn("Compte X inattendu", result["error"])
        self.assertEqual(1, run.call_count)

    def test_search_builds_a_typed_read_only_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, twitter = self._configured(Path(temporary))
            status = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "data": {"user": {"username": "STOOOKEEE"}}}
                ),
                stderr="",
            )
            search = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps({"ok": True, "data": []}), stderr=""
            )
            with (
                patch.object(self.plugin, "X_CONFIG", config),
                patch.object(self.plugin, "TWITTER", twitter),
                patch.object(self.plugin.subprocess, "run", side_effect=[status, search]) as run,
                patch.dict(os.environ, {"XACTIONS_EXPECTED_USERNAME": "STOOOKEEE"}),
            ):
                result = json.loads(
                    self.plugin._handle_search(
                        {
                            "query": "agents IA",
                            "max_results": 3,
                            "search_type": "latest",
                            "language": "fr",
                            "since": "2026-07-01",
                        }
                    )
                )

        self.assertTrue(result["ok"])
        command = run.call_args_list[1].args[0]
        self.assertEqual(
            [
                str(twitter),
                "--compact",
                "search",
                "agents IA",
                "--type",
                "latest",
                "--max",
                "3",
                "--lang",
                "fr",
                "--since",
                "2026-07-01",
                "--json",
            ],
            command,
        )
        self.assertFalse(run.call_args_list[1].kwargs.get("shell", False))


class TwitterReadonlyTests(unittest.TestCase):
    def test_write_command_is_refused_before_binary_execution(self) -> None:
        wrapper = _load_module("twitter_readonly_test", WRAPPER_PATH)
        with patch.object(wrapper, "_reexec_in_private_venv"):
            self.assertEqual(77, wrapper.main(["post", "interdit"]))

    def test_version_manifest_is_pinned(self) -> None:
        versions = yaml.safe_load(
            (REPO_ROOT / "integrations" / "versions.yaml").read_text(encoding="utf-8")
        )
        self.assertRegex(versions["agent_reach"]["commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(versions["xactions"]["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual("0.8.5", versions["twitter_cli"]["version"])


if __name__ == "__main__":
    unittest.main()
