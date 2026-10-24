"""iOS's interface orientation, read from SpringBoard's log, turns the view."""
import unittest

from ictrl import physical
from ictrl.session import DeviceSession


class OrientationTests(unittest.TestCase):
    def test_springboard_line(self):
        line = ("Active interface orientation changing from portrait (1) to landscapeRight (3)  "
                "(message: 'SBTraitArbiterPipelineManager').")
        self.assertEqual(physical._INTERFACE_ORIENTATION.search(line).group(1), "landscapeRight")
        self.assertIsNone(physical._INTERFACE_ORIENTATION.search("Scene will change interface orientation: landscapeRight (3)"))

    def test_session_reports_each_change_once(self):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        seen = []
        session.on_rotation = seen.append
        for name in ("landscapeRight", "landscapeRight", "portrait", "landscapeLeft", "unknown"):
            session._interface_orientation(name)
        self.assertEqual(seen, [-90, 0, 90])
        self.assertEqual(session.rotation, 90)


if __name__ == "__main__":
    unittest.main()
