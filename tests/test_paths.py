"""Moving the files of earlier versions (~/.ictrl) to the Windows folders."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ictrl import paths


class MigrateTests(unittest.TestCase):
    def test_config_and_local_items_are_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy, config, local = Path(tmp, "legacy"), Path(tmp, "roaming"), Path(tmp, "local")
            (legacy / "pairing").mkdir(parents=True)
            (legacy / "pairing" / "identity.json").write_text("{}")
            (legacy / "settings.json").write_text("{}")
            (legacy / "unknown").mkdir()
            with mock.patch.multiple(paths, LEGACY_DIR=legacy, CONFIG_DIR=config, LOCAL_DIR=local, _home=None):
                left = paths.migrate_legacy()
            self.assertTrue((config / "settings.json").exists())
            self.assertTrue((local / "pairing" / "identity.json").exists())
            self.assertFalse((legacy / "settings.json").exists())
            self.assertTrue((legacy / "unknown").exists())     # not ours: left in place
            self.assertEqual(left, [])

    def test_earlier_data_wins_over_files_made_before_the_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy, config, local = Path(tmp, "legacy"), Path(tmp, "roaming"), Path(tmp, "local")
            legacy.mkdir(); config.mkdir()
            (legacy / "settings.json").write_text("old")
            (config / "settings.json").write_text("new")
            with mock.patch.multiple(paths, LEGACY_DIR=legacy, CONFIG_DIR=config, LOCAL_DIR=local, _home=None):
                left = paths.migrate_legacy()
            self.assertEqual((config / "settings.json").read_text(), "old")
            self.assertEqual(left, [])
            self.assertTrue((local / paths.MARKER).exists())

    def test_folders_are_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy, config, local = Path(tmp, "legacy"), Path(tmp, "roaming"), Path(tmp, "local")
            (legacy / "pairing").mkdir(parents=True)
            (legacy / "pairing" / "identity.json").write_text("old")
            (legacy / "pairing" / "device.plist").write_text("record")
            (local / "pairing").mkdir(parents=True)
            (local / "pairing" / "identity.json").write_text("fresh")
            with mock.patch.multiple(paths, LEGACY_DIR=legacy, CONFIG_DIR=config, LOCAL_DIR=local, _home=None):
                paths.migrate_legacy()
            self.assertEqual((local / "pairing" / "identity.json").read_text(), "old")
            self.assertEqual((local / "pairing" / "device.plist").read_text(), "record")
            self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
