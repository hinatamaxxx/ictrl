"""Opening an app on a locked phone says so at once instead of timing out."""
import unittest
from unittest import mock

from utsushite.session import DeviceSession, SessionError


class LaunchLockedTests(unittest.IsolatedAsyncioTestCase):
    def session(self, locked):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        session.rsd = mock.MagicMock()
        session.rsd.get_value = mock.AsyncMock(return_value=locked)
        return session

    async def test_locked_phone_is_reported(self):
        session = self.session(True)
        with mock.patch("pymobiledevice3.remote.core_device.app_service.AppServiceService") as service:
            with self.assertRaisesRegex(SessionError, "ロック"):
                await session.launch_app("com.apple.Preferences")
        service.assert_not_called()
        session.rsd.get_value.assert_awaited_once_with(key="PasswordProtected")

    async def test_unlocked_phone_launches(self):
        session = self.session(False)
        with mock.patch("pymobiledevice3.remote.core_device.app_service.AppServiceService") as service:
            svc = service.return_value.__aenter__.return_value
            svc.launch_application = mock.AsyncMock()
            self.assertEqual(await session.launch_app("com.apple.Preferences"), {"ok": True})
        svc.launch_application.assert_awaited_once_with("com.apple.Preferences", kill_existing=False)


if __name__ == "__main__":
    unittest.main()
