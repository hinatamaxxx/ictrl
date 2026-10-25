"""Switching the page's language back from English looks each English text up
(i18n.js JA_OF): no two Japanese texts may share one English translation."""
import json
import re
import unittest
from collections import defaultdict
from pathlib import Path

I18N = Path(__file__).resolve().parent.parent / "utsushite" / "ui" / "i18n.js"
STRING = r'"(?:[^"\\]|\\.)*"'


class TranslationTests(unittest.TestCase):
    def test_each_english_text_has_one_japanese_source(self):
        source = I18N.read_text("utf-8")
        table = source[source.index("const EN = {"):]
        pairs = [(json.loads(ja), json.loads(en))
                 for ja, en in re.findall(rf"({STRING}):\s*({STRING})", table)]
        self.assertGreater(len(pairs), 100)
        sources = defaultdict(list)
        for ja, en in pairs:
            sources[en].append(ja)
        self.assertEqual({en: jas for en, jas in sources.items() if len(jas) > 1}, {})


if __name__ == "__main__":
    unittest.main()
