"""setup.ps1 -Claude registers the Stop hook once, keeping other settings."""
import json
import tempfile
import unittest
from pathlib import Path

from ictrl.__main__ import install_claude_hook, remove_claude_hook


class ClaudeHookTests(unittest.TestCase):
    def test_added_once_and_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}))
            install_claude_hook(path)
            install_claude_hook(path)
            data = json.loads(path.read_text("utf-8"))
        commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(data["model"], "opus")
        self.assertEqual(sum("ictrl stop-hook" in c for c in commands), 1)
        self.assertIn("x", commands)

    def test_creates_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new" / "settings.json"
            install_claude_hook(path)
            self.assertIn("Stop", json.loads(path.read_text("utf-8"))["hooks"])


class RemoveHookTests(unittest.TestCase):
    def test_removed_and_others_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}))
            install_claude_hook(path)
            remove_claude_hook(path)
            data = json.loads(path.read_text("utf-8"))
        self.assertEqual([h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]], ["x"])


if __name__ == "__main__":
    unittest.main()
