"""The page's display language is a setting: auto (the browser's), ja or en."""
import unittest
from unittest import mock

from utsushite.manager import Manager


class LanguageSettingTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        with mock.patch.object(Manager, "_load_settings", return_value={}), \
                mock.patch.object(Manager, "_load_known", return_value={}):
            m = Manager()
        m._settings_path = mock.Mock()
        return m

    async def test_auto_by_default(self):
        self.assertEqual(self.make().settings["language"], "auto")

    async def test_english_is_kept(self):
        m = self.make()
        self.assertEqual((await m.update_settings({"language": "en"}))["language"], "en")

    async def test_an_unknown_language_is_refused(self):
        m = self.make()
        with self.assertRaises(ValueError):
            await m.update_settings({"language": "fr"})
        self.assertEqual(m.settings["language"], "auto")


if __name__ == "__main__":
    unittest.main()
