"""The Claude Code plugin: its files exist and every install is hash-checked."""
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PluginTests(unittest.TestCase):
    def test_manifests_point_at_the_entry_point(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        args = plugin["mcpServers"]["utsushite"]["args"]
        self.assertTrue(any(a.endswith("plugin\\utsushite.cmd") for a in args))
        hooks = json.loads((ROOT / "plugin" / "hooks.json").read_text("utf-8"))
        self.assertIn("plugin/utsushite.cmd", hooks["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertTrue((ROOT / "plugin" / "utsushite.cmd").exists())
        self.assertTrue((ROOT / "plugin" / "launch.py").exists())

    def test_codex_manifest_points_at_the_entry_point(self):
        plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text("utf-8"))
        mcp = json.loads((ROOT / plugin["mcpServers"]).read_text("utf-8"))["mcpServers"]["utsushite"]
        self.assertEqual(mcp["cwd"], ".")                     # relative to the plugin folder
        self.assertIn(r"plugin\utsushite.cmd", mcp["args"])
        market = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text("utf-8"))
        self.assertEqual(market["plugins"][0]["source"]["path"], "./")
        # Claude Code also reads a plugin's .mcp.json: none at the root.
        self.assertFalse((ROOT / ".mcp.json").exists())

    def test_entry_point_is_crlf_and_ascii(self):
        data = (ROOT / "plugin" / "utsushite.cmd").read_bytes()
        data.decode("ascii")                      # cmd reads it in the console's code page
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_every_requirement_has_a_hash(self):
        text = (ROOT / "requirements.txt").read_text("utf-8")
        entries = re.split(r"\n(?=[A-Za-z])", re.sub(r"(?m)^#.*\n", "", text).strip())
        for entry in entries:
            with self.subTest(entry.splitlines()[0]):
                self.assertRegex(entry, r"--hash=sha256:[0-9a-f]{64}")

    def test_bundled_wheels_match_their_hashes(self):
        text = (ROOT / "requirements.txt").read_text("utf-8")
        for wheel in (ROOT / "wheels").glob("*.whl"):
            with self.subTest(wheel.name):
                self.assertIn(hashlib.sha256(wheel.read_bytes()).hexdigest(), text)


if __name__ == "__main__":
    unittest.main()
