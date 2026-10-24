"""iOS puts its own input mode back when another field or app takes the keys
or the keyboard is registered again; the next typed key is preceded by the
mode the PC asked for (Windows "A", iPhone typed Japanese)."""
import unittest
from unittest import mock

from ictrl import keys
from ictrl.session import DeviceSession

EISU = keys.usage_for_name("eisu")
KANA = keys.usage_for_name("kana")
A = keys.usage_for_name("a")


class InputModeReassertTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        session.keyboard_id = 1
        session._ensure_keyboard = mock.AsyncMock()
        strokes = []

        async def stroke(usage, mods):
            strokes.append(usage)
        session._stroke = stroke
        return session, strokes

    async def test_mode_is_sent_again_after_a_touch(self):
        session, strokes = self.make()
        await session._key_combo("eisu", [])
        await session._key_combo("a", [])
        self.assertEqual(strokes, [EISU, A])                # nothing changed: no extra key
        session._input_mode_stale = True                    # what a touch / button / new keyboard sets
        await session._key_combo("a", [])
        self.assertEqual(strokes, [EISU, A, EISU, A])
        await session._key_combo("a", [])
        self.assertEqual(strokes, [EISU, A, EISU, A, A])    # once per change of field, not per key

    async def test_the_latest_mode_is_the_one_sent(self):
        session, strokes = self.make()
        await session._key_combo("eisu", [])
        await session._key_combo("kana", [])
        session._input_mode_stale = True
        await session._key_combo("a", [])
        self.assertEqual(strokes, [EISU, KANA, KANA, A])

    async def test_no_mode_known_sends_nothing_extra(self):
        session, strokes = self.make()
        session._input_mode_stale = True
        await session._key_combo("a", [])
        self.assertEqual(strokes, [A])


if __name__ == "__main__":
    unittest.main()
