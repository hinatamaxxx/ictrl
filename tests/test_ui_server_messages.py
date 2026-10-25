"""The page shows the server's (Japanese) messages in English through the
SERVER table in ui/i18n.js: each entry must still be a message the server sends."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ictrl"


def server_keys():
    source = (ROOT / "ui" / "i18n.js").read_text("utf-8")
    table = source[source.index("const SERVER = {"):source.index("const EN_HTML")]
    return [json.loads(m.group(1)) for m in re.finditer(r'^\s*("(?:[^"\\]|\\.)*"):', table, re.M)]


def python_text():
    text = "".join(p.read_text("utf-8") for p in ROOT.glob("*.py"))
    # Adjacent literals across lines ("..." \n "...", also f"...") read as one.
    return re.sub(r'["\']\)?\s*\n\s*\(?f?["\']', "", text)


class ServerMessageTests(unittest.TestCase):
    def test_every_entry_is_still_sent(self):
        text = python_text()
        for key in server_keys():
            for fragment in re.split(r"\{\w+\}", key):
                with self.subTest(key=key):
                    self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
