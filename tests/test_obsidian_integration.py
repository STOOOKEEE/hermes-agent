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


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = (
    REPO_ROOT
    / "profiles"
    / "obsidian"
    / "plugins"
    / "obsidian-vault"
    / "__init__.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("obsidian_vault_test", PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ObsidianVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.profile = self.root / "profile"
        self.env = {
            "HERMES_PROFILE": "obsidian",
            "HERMES_PROFILE_HOME": str(self.profile),
            "OBSIDIAN_VAULT_PATH": str(self.vault),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_path_escape_and_git_directory_are_refused(self) -> None:
        with patch.dict(os.environ, self.env, clear=False):
            traversal = json.loads(
                self.plugin._handle_write({"path": "../secret.md", "content": "x"})
            )
            git = json.loads(
                self.plugin._handle_write({"path": ".git/config", "content": "x"})
            )
        self.assertFalse(traversal["success"])
        self.assertFalse(git["success"])
        self.assertFalse((self.root / "secret.md").exists())

    def test_write_is_atomic_and_backs_up_existing_note_outside_vault(self) -> None:
        note = self.vault / "Projets" / "Hermes.md"
        note.parent.mkdir()
        note.write_text("ancienne version", encoding="utf-8")

        with patch.dict(os.environ, self.env, clear=False):
            result = json.loads(
                self.plugin._handle_write(
                    {"path": "Projets/Hermes.md", "content": "nouvelle version"}
                )
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["backup_created"])
        self.assertEqual("nouvelle version", note.read_text(encoding="utf-8"))
        backups = list((self.profile / "backups").rglob("Hermes.md"))
        self.assertEqual(1, len(backups))
        self.assertEqual("ancienne version", backups[0].read_text(encoding="utf-8"))

    def test_search_and_append_notes(self) -> None:
        note = self.vault / "Inbox.md"
        note.write_text("Une idée sur Hermes\n", encoding="utf-8")
        with patch.dict(os.environ, self.env, clear=False):
            search = json.loads(self.plugin._handle_search({"query": "hermes"}))
            append = json.loads(
                self.plugin._handle_append({"path": "Inbox.md", "content": "À revoir"})
            )
        self.assertEqual("Inbox.md", search["matches"][0]["path"])
        self.assertTrue(append["success"])
        self.assertEqual("Une idée sur Hermes\nÀ revoir", note.read_text(encoding="utf-8"))

    def test_tools_are_unavailable_outside_obsidian_profile(self) -> None:
        with patch.dict(
            os.environ,
            {**self.env, "HERMES_PROFILE": "twitter"},
            clear=False,
        ):
            result = json.loads(self.plugin._handle_list({}))
        self.assertFalse(result["success"])
        self.assertIn("hors du profil obsidian", result["error"])

    def test_git_sync_requires_a_scoped_approval(self) -> None:
        note = self.vault / "Inbox.md"
        note.write_text("première version", encoding="utf-8")
        with (
            patch.dict(os.environ, self.env, clear=False),
            patch.object(self.plugin, "_git_root", return_value=self.vault),
            patch.object(
                self.plugin,
                "_working_tree_fingerprint",
                side_effect=["state-one", "state-two"],
            ),
        ):
            approval = self.plugin._sync_approval(
                self.plugin.SYNC_TOOL_NAME, {"message": "notes: mise à jour"}
            )
            changed = self.plugin._sync_approval(
                self.plugin.SYNC_TOOL_NAME, {"message": "notes: mise à jour"}
            )
        self.assertEqual("approve", approval["action"])
        self.assertIn("notes: mise à jour", approval["message"])
        self.assertTrue(approval["rule_key"].startswith("obsidian_git_sync:"))
        self.assertNotEqual(approval["rule_key"], changed["rule_key"])

    @unittest.skipUnless(shutil.which("git"), "Git absent")
    def test_working_tree_fingerprint_tracks_uncommitted_content(self) -> None:
        subprocess.run(
            ["git", "init", "--quiet", str(self.vault)],
            check=True,
            capture_output=True,
            text=True,
        )
        note = self.vault / "Inbox.md"
        note.write_text("première version", encoding="utf-8")
        first = self.plugin._working_tree_fingerprint(self.vault)
        note.write_text("seconde version", encoding="utf-8")
        second = self.plugin._working_tree_fingerprint(self.vault)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
