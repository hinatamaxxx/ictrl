import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows IME API")
class WinImeTests(unittest.TestCase):
    def test_state_shape(self):
        from ictrl import winime
        state = winime.state()
        self.assertIn("available", state)
        if state["available"]:
            self.assertIsInstance(state["japanese"], bool)
            self.assertIsInstance(state["conversion_mode"], int)


if __name__ == "__main__":
    unittest.main()
