from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.configure_discord import (
    ConfigurationError,
    _create_or_update_profiles,
    _env_has_value,
    _write_yaml_atomic,
    render_config,
    validate_manifest,
)


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


if __name__ == "__main__":
    unittest.main()
