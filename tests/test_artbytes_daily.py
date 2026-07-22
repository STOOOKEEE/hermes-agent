from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_PATH = REPO_ROOT / "automations" / "artbytes_daily_context.py"
CONFIGURE_PATH = REPO_ROOT / "scripts" / "configure_artbytes_daily.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ArtbytesContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module("artbytes_daily_context_test", CONTEXT_PATH)

    def test_snowflake_increases_with_time(self) -> None:
        first = datetime(2026, 7, 22, 8, tzinfo=timezone.utc)
        second = datetime(2026, 7, 22, 9, tzinfo=timezone.utc)
        self.assertLess(
            int(self.module.snowflake_from_time(first)),
            int(self.module.snowflake_from_time(second)),
        )

    def test_sampling_keeps_priority_messages(self) -> None:
        messages = [
            {
                "id": str(index),
                "timestamp": f"2026-07-22T10:{index % 60:02d}:00",
                "priority": index == 0,
            }
            for index in range(self.module.MAX_SELECTED_MESSAGES + 20)
        ]
        selected = self.module._sample(messages)
        self.assertEqual(self.module.MAX_SELECTED_MESSAGES, len(selected))
        self.assertIn("0", {message["id"] for message in selected})


class ConfigureArtbytesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module("configure_artbytes_daily_test", CONFIGURE_PATH)

    def test_existing_placeholder_is_reused(self) -> None:
        jobs = [{"id": "old", "name": "artbytes-twitter-daily"}]
        managed = self.module._managed_index(jobs)
        self.assertEqual("old", managed["artbytes-daily-morning"]["id"])

    def test_load_jobs_current_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.json"
            path.write_text(
                json.dumps({"jobs": [{"id": "a", "name": "other"}]}),
                encoding="utf-8",
            )
            jobs = self.module._load_jobs(path)
        self.assertEqual("a", jobs[0]["id"])


if __name__ == "__main__":
    unittest.main()
