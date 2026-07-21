from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIEF_PATH = REPO_ROOT / "automations" / "x_editorial_brief.py"
CONFIGURE_PATH = REPO_ROOT / "scripts" / "configure_x_automation.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EditorialBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module("x_editorial_brief_test", BRIEF_PATH)

    def test_prompt_is_read_only_and_bounded(self) -> None:
        prompt = self.module.build_prompt(datetime(2026, 7, 21, 9, 15))
        self.assertIn("strictement en lecture seule", prompt)
        self.assertIn("260 caractères", prompt)
        self.assertIn("Publie exactement ce brouillon", prompt)
        self.assertNotIn("xactions_post_tweet", prompt)

    def test_oneshot_only_loads_reader_and_web(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".hermes"
            (root / "profiles" / "twitter").mkdir(parents=True)
            binary = Path(temporary) / "hermes"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="brouillon sûr\n", stderr=""
            )
            with (
                patch.dict(os.environ, {"HERMES_ROOT_HOME": str(root)}),
                patch.object(self.module, "_hermes_binary", return_value=binary),
                patch.object(self.module.subprocess, "run", return_value=completed) as run,
            ):
                status, output = self.module.run(datetime(2026, 7, 21, 17, 30))

        self.assertEqual(0, status)
        self.assertEqual("brouillon sûr", output)
        command = run.call_args.args[0]
        self.assertEqual("agent_reach_reader,web", command[command.index("--toolsets") + 1])
        self.assertNotIn("xactions_publisher", command)
        self.assertEqual("twitter", run.call_args.kwargs["env"]["HERMES_PROFILE"])


class ConfigureAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module("configure_x_automation_test", CONFIGURE_PATH)

    def test_rejects_invalid_channel(self) -> None:
        with self.assertRaises(self.module.AutomationError):
            self.module.configure(
                channel_id="pas-un-id",
                hermes_home=Path("/tmp/hermes-test"),
                hermes_bin=Path("/tmp/hermes"),
                apply=False,
            )

    def test_managed_index_refuses_duplicates(self) -> None:
        jobs = [
            {"id": "one", "name": "x-editorial-morning"},
            {"id": "two", "name": "x-editorial-morning"},
        ]
        with self.assertRaises(self.module.AutomationError):
            self.module._managed_index(jobs)

    def test_loads_current_jobs_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.json"
            path.write_text(
                json.dumps({"jobs": [{"id": "abc", "name": "unrelated"}]}),
                encoding="utf-8",
            )
            jobs = self.module._load_jobs(path)
        self.assertEqual("abc", jobs[0]["id"])


if __name__ == "__main__":
    unittest.main()
