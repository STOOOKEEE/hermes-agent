from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.configure_discord import (
    ConfigurationError,
    _create_or_update_profiles,
    _deploy_multiplex_runtime_plugins,
    _deploy_profile_plugins,
    _enable_profile_plugins,
    _env_has_value,
    _strip_profile_gateway_credentials,
    _set_profile_discord_toolsets,
    _write_yaml_atomic,
    render_config,
    validate_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class ConfigureDiscordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        for profile in ("twitter", "crypto"):
            profile_dir = self.repo / "profiles" / profile
            profile_dir.mkdir(parents=True)
            (profile_dir / "SOUL.md").write_text(f"mission {profile}\n", encoding="utf-8")

        self.manifest = {
            "version": 1,
            "discord": {
                "guild_id": "123456789012345678",
                "restrict_to_managed_channels": True,
                "group_sessions_per_user": True,
                "channels": [
                    {
                        "name": "twitter",
                        "channel_id": "223456789012345678",
                        "profile": "twitter",
                        "description": "Opérations Twitter contrôlées.",
                        "respond_without_mention": True,
                        "use_threads": False,
                    },
                    {
                        "name": "crypto",
                        "channel_id": "323456789012345678",
                        "profile": "crypto",
                        "description": "Surveillance crypto.",
                        "respond_without_mention": False,
                        "use_threads": True,
                    },
                ],
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_validate_manifest(self) -> None:
        channels = validate_manifest(self.manifest, self.repo)
        self.assertEqual(["twitter", "crypto"], [item["profile"] for item in channels])

    def test_invalid_profile_toolset_is_rejected(self) -> None:
        self.manifest["discord"]["channels"][0]["toolsets"] = ["web", "bad tool"]
        with self.assertRaisesRegex(ConfigurationError, "nom invalide"):
            validate_manifest(self.manifest, self.repo)

    def test_template_placeholders_are_only_allowed_explicitly(self) -> None:
        template = {
            **self.manifest,
            "discord": {
                **self.manifest["discord"],
                "guild_id": "REPLACE_WITH_DISCORD_SERVER_ID",
                "channels": [
                    {
                        **self.manifest["discord"]["channels"][0],
                        "channel_id": "REPLACE_WITH_TWITTER_CHANNEL_ID",
                    }
                ],
            },
        }
        with self.assertRaises(ConfigurationError):
            validate_manifest(template, self.repo)
        validate_manifest(template, self.repo, allow_placeholders=True)

    def test_duplicate_channel_id_is_rejected(self) -> None:
        self.manifest["discord"]["channels"][1]["channel_id"] = (
            self.manifest["discord"]["channels"][0]["channel_id"]
        )
        with self.assertRaisesRegex(ConfigurationError, "dupliqué"):
            validate_manifest(self.manifest, self.repo)

    def test_default_channel_uses_main_without_profile_route_or_soul(self) -> None:
        general = {
            "name": "general",
            "channel_id": "423456789012345678",
            "profile": "default",
            "description": "Hermes principal.",
            "respond_without_mention": True,
            "use_threads": False,
        }
        self.manifest["discord"]["channels"].insert(0, general)

        channels = validate_manifest(self.manifest, self.repo)
        rendered = render_config({}, self.manifest, channels)

        self.assertEqual(
            ["discord-channel/twitter", "discord-channel/crypto"],
            [route["name"] for route in rendered["gateway"]["profile_routes"]],
        )
        self.assertEqual(
            [
                "423456789012345678",
                "223456789012345678",
                "323456789012345678",
            ],
            rendered["discord"]["allowed_channels"],
        )
        self.assertIn(
            "423456789012345678", rendered["discord"]["free_response_channels"]
        )

    @patch("scripts.configure_discord.subprocess.run")
    def test_default_channel_does_not_create_a_profile(self, run) -> None:
        channels = [
            {
                "name": "general",
                "channel_id": "423456789012345678",
                "profile": "default",
                "description": "Hermes principal.",
                "respond_without_mention": True,
                "use_threads": False,
                "toolsets": None,
            }
        ]
        _create_or_update_profiles(
            channels=channels,
            repo_root=self.repo,
            hermes_home=self.repo / "hermes-home",
            hermes_bin="hermes-test",
            timestamp="20260101T000000Z",
        )
        run.assert_not_called()
        self.assertFalse((self.repo / "hermes-home" / "profiles" / "default").exists())

    def test_render_replaces_managed_routes_and_preserves_others(self) -> None:
        existing = {
            "model": {"provider": "example"},
            "gateway": {
                "profile_routes": [
                    {
                        "name": "telegram-existing",
                        "platform": "telegram",
                        "chat_id": "-100123",
                        "profile": "default",
                    },
                    {
                        "name": "discord-channel/old",
                        "platform": "discord",
                        "chat_id": "423456789012345678",
                        "profile": "old",
                    },
                ]
            },
            "discord": {
                "free_response_channels": [
                    "423456789012345678",
                    "523456789012345678",
                ],
                "no_thread_channels": ["423456789012345678"],
            },
        }
        channels = validate_manifest(self.manifest, self.repo)
        rendered = render_config(existing, self.manifest, channels)

        self.assertTrue(rendered["gateway"]["multiplex_profiles"])
        routes = rendered["gateway"]["profile_routes"]
        self.assertEqual("telegram-existing", routes[0]["name"])
        self.assertEqual(
            ["discord-channel/twitter", "discord-channel/crypto"],
            [route["name"] for route in routes[1:]],
        )
        self.assertEqual(
            ["523456789012345678", "223456789012345678"],
            rendered["discord"]["free_response_channels"],
        )
        self.assertEqual(
            ["223456789012345678"],
            rendered["discord"]["no_thread_channels"],
        )
        self.assertEqual(
            ["223456789012345678", "323456789012345678"],
            rendered["discord"]["allowed_channels"],
        )
        self.assertTrue(rendered["group_sessions_per_user"])
        self.assertEqual({"provider": "example"}, rendered["model"])

    def test_env_preflight_does_not_need_to_expose_value(self) -> None:
        env_path = self.repo / ".env"
        env_path.write_text(
            "# secret\nDISCORD_BOT_TOKEN='real-secret'\nDISCORD_ALLOWED_USERS=\n",
            encoding="utf-8",
        )
        self.assertTrue(_env_has_value("DISCORD_BOT_TOKEN", env_path))
        self.assertFalse(_env_has_value("DISCORD_ALLOWED_USERS", env_path))

    def test_atomic_write_uses_private_mode_for_new_config(self) -> None:
        target = self.repo / "new-home" / "config.yaml"
        _write_yaml_atomic(target, {"gateway": {"multiplex_profiles": True}})
        self.assertEqual(0o600, target.stat().st_mode & 0o777)

    @patch("scripts.configure_discord.subprocess.run")
    def test_profile_is_cloned_then_receives_versioned_mission(self, run) -> None:
        channels = validate_manifest(self.manifest, self.repo)[:1]
        hermes_home = self.repo / "hermes-home"

        _create_or_update_profiles(
            channels=channels,
            repo_root=self.repo,
            hermes_home=hermes_home,
            hermes_bin="hermes-test",
            timestamp="20260101T000000Z",
        )

        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(
            [
                "hermes-test",
                "profile",
                "create",
                "twitter",
                "--clone-from",
                "default",
                "--no-alias",
                "--description",
                "Opérations Twitter contrôlées.",
            ],
            command,
        )
        self.assertEqual(
            "mission twitter\n",
            (hermes_home / "profiles" / "twitter" / "SOUL.md").read_text(
                encoding="utf-8"
            ),
        )

    def test_profile_plugin_is_deployed_and_enabled(self) -> None:
        source = self.repo / "profiles" / "twitter"
        plugin = source / "plugins" / "publisher"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: publisher\n", encoding="utf-8")
        (plugin / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")

        target = self.repo / "hermes-home" / "profiles" / "twitter"
        target.mkdir(parents=True)
        (target / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - existing\n",
            encoding="utf-8",
        )

        _deploy_profile_plugins(
            source_profile=source,
            target_profile=target,
            timestamp="20260101T000000Z",
            discord_toolsets=["agent_reach_reader", "web", "no_mcp"],
        )

        deployed = target / "plugins" / "publisher"
        self.assertTrue((deployed / "plugin.yaml").is_file())
        rendered = yaml.safe_load(
            (target / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(["existing", "publisher"], rendered["plugins"]["enabled"])
        self.assertEqual(
            ["agent_reach_reader", "web", "no_mcp"],
            rendered["platform_toolsets"]["discord"],
        )

        (plugin / "__init__.py").write_text(
            "VERSION = 2\ndef register(ctx): pass\n", encoding="utf-8"
        )
        _deploy_profile_plugins(
            source_profile=source,
            target_profile=target,
            timestamp="20260102T000000Z",
        )
        self.assertFalse(any((target / "plugins").glob("*.bak.discord-*")))
        self.assertTrue(
            (
                target
                / "backups"
                / "plugins"
                / "publisher.bak.discord-20260102T000000Z"
            ).is_dir()
        )

    def test_multiplex_runtime_plugin_is_deployed_and_enabled_at_gateway(self) -> None:
        source = self.repo / "profiles" / "twitter" / "plugins" / "reader"
        source.mkdir(parents=True)
        (source / "__init__.py").write_text(
            "def register(ctx): pass\n", encoding="utf-8"
        )
        (source / "plugin.yaml").write_text(
            "name: reader\n"
            "version: 1.0.0\n"
            "kind: standalone\n"
            "multiplex_global: true\n",
            encoding="utf-8",
        )
        hermes_home = self.repo / "hermes-home"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - existing\n", encoding="utf-8"
        )

        names = _deploy_multiplex_runtime_plugins(
            channels=validate_manifest(self.manifest, self.repo),
            repo_root=self.repo,
            hermes_home=hermes_home,
            timestamp="20260101T000000Z",
            update_config=True,
        )

        self.assertEqual(["reader"], names)
        self.assertTrue(
            (hermes_home / "plugins" / "reader" / "plugin.yaml").is_file()
        )
        config = yaml.safe_load(
            (hermes_home / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(["existing", "reader"], config["plugins"]["enabled"])

    def test_plugin_config_rejects_malformed_enabled_list(self) -> None:
        with self.assertRaises(ConfigurationError):
            _enable_profile_plugins({"plugins": {"enabled": "publisher"}}, ["publisher"])

    def test_profile_discord_toolsets_replace_inherited_composite(self) -> None:
        rendered = _set_profile_discord_toolsets(
            {
                "platform_toolsets": {
                    "discord": ["hermes-discord"],
                    "cli": ["terminal"],
                }
            },
            ["agent_reach_reader", "web", "no_mcp"],
        )
        self.assertEqual(
            ["agent_reach_reader", "web", "no_mcp"],
            rendered["platform_toolsets"]["discord"],
        )
        self.assertEqual(["terminal"], rendered["platform_toolsets"]["cli"])

    def test_profile_transport_tokens_are_removed_but_provider_keys_remain(self) -> None:
        env_path = self.repo / "profile.env"
        env_path.write_text(
            "OPENROUTER_API_KEY=provider-secret\n"
            "TELEGRAM_BOT_TOKEN=telegram-secret\n"
            "DISCORD_BOT_TOKEN=discord-secret\n"
            "DISCORD_ALLOWED_USERS=123\n"
            "XACTIONS_EXPECTED_USERNAME=STOOOKEEE\n",
            encoding="utf-8",
        )
        env_path.chmod(0o600)

        removed = _strip_profile_gateway_credentials(
            env_path, "20260101T000000Z"
        )

        rendered = env_path.read_text(encoding="utf-8")
        self.assertIn("OPENROUTER_API_KEY=provider-secret", rendered)
        self.assertIn("XACTIONS_EXPECTED_USERNAME=STOOOKEEE", rendered)
        self.assertNotIn("telegram-secret", rendered)
        self.assertNotIn("discord-secret", rendered)
        self.assertEqual(
            ["TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_USERS"],
            removed,
        )
        self.assertTrue(
            (self.repo / "profile.env.bak.discord-20260101T000000Z").is_file()
        )

    @patch("scripts.configure_discord._create_or_update_profiles")
    def test_profiles_only_accepts_template_without_writing_gateway(self, deploy) -> None:
        from scripts.configure_discord import main

        original = REPO_ROOT / "config" / "discord-channels.example.yaml"
        result = main(
            [
                "--manifest",
                str(original),
                "--hermes-home",
                str(self.repo / "hermes-home"),
                "--profiles-only",
                "--apply",
            ]
        )
        self.assertEqual(0, result)
        deploy.assert_called_once()
        self.assertFalse((self.repo / "hermes-home" / "config.yaml").exists())


if __name__ == "__main__":
    unittest.main()
