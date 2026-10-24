"""The dock's lock button locks the phone and never wakes it."""
import unittest
from unittest import mock

from ictrl.session import DeviceSession, SessionError


class LockOnlyTests(unittest.IsolatedAsyncioTestCase):
    def session(self, locked):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        session.rsd = mock.MagicMock()
        session.rsd.get_value = mock.AsyncMock(return_value=locked)
        session._button = mock.AsyncMock(return_value={"ok": True})
        return session

    async def test_unlocked_phone_is_locked(self):
        session = self.session(False)
        self.assertEqual((await session._execute({"op": "lock"}))["pressed"], True)
        session._button.assert_awaited_once_with("lock", "press")
        session.rsd.get_value.assert_awaited_once_with(key="PasswordProtected")

    async def test_locked_phone_is_not_woken(self):
        session = self.session(True)
        result = await session._execute({"op": "lock"})
        self.assertEqual((result["pressed"], result["already_locked"]), (False, True))
        session._button.assert_not_awaited()

    async def test_unknown_state_presses_nothing(self):
        session = self.session(None)
        with self.assertRaises(SessionError):
            await session._execute({"op": "lock"})
        session._button.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
