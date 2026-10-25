import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utsushite import window


@unittest.skipUnless(os.name == "nt" and window.csc().exists(), "needs the .NET Framework csc")
class WindowBuildTest(unittest.TestCase):
    def test_builds_with_the_windows_csc_and_skips_when_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch.object(window, "OUT_DIR", out), mock.patch.object(window, "EXE", out / "UtsushiteWindow.exe"):
                exe = window.build()
                self.assertTrue(exe.exists())
                for dll in window._DLLS:
                    self.assertTrue((out / dll).exists(), dll)
                self.assertTrue(window.is_current())
                built = exe.stat().st_mtime
                window.build()
                self.assertEqual(exe.stat().st_mtime, built)


class DecoderConfigTest(unittest.TestCase):
    def test_coded_size_is_given_to_the_decoder(self):
        # Edge/WebView2 (Media Foundation) reported a 1280x720 visible rect
        # without it, so only the top of the screen was drawn.
        js = (Path(window.__file__).parent / "ui" / "app.js").read_text("utf-8")
        self.assertIn("config.codedWidth = cfg.width; config.codedHeight = cfg.height;", js)


if __name__ == "__main__":
    unittest.main()
