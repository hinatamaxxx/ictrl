"""Input guards and fresh screenshots used by XCTest passcode entry."""
import asyncio
import io
import unittest
from unittest import mock

from PIL import Image

from utsushite.session import DeviceSession


class PasscodeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = DeviceSession("test-phone", "wifi", lambda _info: None)
        self.session.info.state = "ready"

    async def test_call_snapshot_uses_instruments_without_video_or_wda(self):
        from pymobiledevice3.services.dvt.instruments import dvt_provider, screenshot
        self.session.info.video_paused = "call"
        buffer = io.BytesIO()
        Image.new("RGB", (300, 600), "black").save(buffer, "PNG")
        dvt = mock.MagicMock()
        dvt.__aenter__ = mock.AsyncMock(return_value=dvt)
        dvt.__aexit__ = mock.AsyncMock(return_value=False)
        shot = mock.MagicMock()
        shot.__aenter__ = mock.AsyncMock(return_value=shot)
        shot.__aexit__ = mock.AsyncMock(return_value=False)
        shot.get_screenshot = mock.AsyncMock(return_value=buffer.getvalue())
        with mock.patch.object(dvt_provider, "DvtProvider", return_value=dvt), \
                mock.patch.object(screenshot, "Screenshot", return_value=shot):
            png, width, height = await self.session.screen_png(300)
        self.assertEqual((width, height), (150, 300))
        self.assertEqual(Image.open(io.BytesIO(png)).size, (150, 300))
        shot.get_screenshot.assert_awaited_once()
        self.assertIsNone(self.session._wda)

    async def test_user_submission_and_completion_invalidate_secret_confirmation(self):
        self.session._execute = mock.AsyncMock(return_value={"ok": True})
        worker = asyncio.create_task(self.session._input_worker())
        try:
            future = self.session.submit({"op": "key", "key": "1"})
            self.assertEqual(self.session.passcode_input_generation, 1)
            await future
            self.assertEqual(self.session.passcode_input_generation, 2)
            await self.session.run({"op": "show_phone_keyboard"})
            self.assertEqual(self.session.passcode_input_generation, 2)
            await self.session._physical_touch()
            self.assertEqual(self.session.passcode_input_generation, 3)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_xctest_secret_strokes_do_not_register_keyboard_or_run_shortcut(self):
        self.session._ensure_keyboard = mock.AsyncMock()
        self.session._stroke = mock.AsyncMock()
        command = {"op": "secret_strokes", "register_keyboard": False,
                   "guard": lambda: True, "prepare": mock.AsyncMock(return_value=[(30, False)])}
        await self.session._execute(command)
        self.session._ensure_keyboard.assert_not_awaited()
        self.session._stroke.assert_awaited_once_with(30, [])
        self.assertEqual(command, {})


if __name__ == "__main__":
    unittest.main()
