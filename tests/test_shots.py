"""Screenshots go to Windows' Screenshots folder with a readable name."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from utsushite import shots


class SaveTests(unittest.TestCase):
    def test_name_and_suffix_when_taken(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(shots, "folder", return_value=Path(tmp)):
            now = time.mktime((2026, 10, 21, 16, 35, 12, 0, 0, -1))
            first = shots.save(b"png1", now)
            second = shots.save(b"png2", now)
            self.assertEqual(first.name, "iPhone 2026-10-21 163512.png")
            self.assertEqual(second.name, "iPhone 2026-10-21 163512 (2).png")
            self.assertEqual(first.read_bytes(), b"png1")
            self.assertEqual(shots.last_saved, second)

    def test_saved_in_the_orientation_the_window_shows(self):
        import io
        from PIL import Image

        def png(w, h):
            buf = io.BytesIO()
            Image.new("RGB", (w, h)).save(buf, "PNG")
            return buf.getvalue()

        def size(data):
            return Image.open(io.BytesIO(data)).size

        portrait, landscape = png(1206, 2622), png(2622, 1206)
        self.assertIs(shots.as_viewed(portrait, 0), portrait)            # already as shown
        self.assertEqual(size(shots.as_viewed(landscape, 0)), (1206, 2622))  # display off
        self.assertEqual(size(shots.as_viewed(portrait, 90)), (2622, 1206))
        self.assertIs(shots.as_viewed(landscape, -90), landscape)

    def test_default_is_an_utsushite_folder_in_pictures(self):
        self.assertEqual(shots.default_folder().name, "utsushite")

    def test_custom_folder_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "shots"
            with mock.patch.object(shots, "custom_dir", str(target)):
                self.assertEqual(shots.folder(), target)
                self.assertTrue(target.is_dir())
            self.assertIsNone(shots.validate_dir(""))
            self.assertEqual(shots.validate_dir(str(target)), str(target))
            with self.assertRaises(ValueError):
                shots.validate_dir("relative\path")
            (Path(tmp) / "file.txt").write_text("x")
            with self.assertRaises(ValueError):
                shots.validate_dir(str(Path(tmp) / "file.txt"))


if __name__ == "__main__":
    unittest.main()
