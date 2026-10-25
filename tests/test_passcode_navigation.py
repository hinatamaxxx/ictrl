"""Fresh Instruments captures for XCTest, without device/secret I/O."""
import io
import unittest
from unittest import mock

from PIL import Image

from utsushite.session import DeviceSession


class PasscodeCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = DeviceSession("test-phone", "wifi", lambda _info: None)
        self.session.info.state = "ready"

    async def test_passcode_capture_always_requests_instruments_even_with_live_video(self):
        from pymobiledevice3.services.dvt.instruments import dvt_provider, screenshot
        buffer = io.BytesIO()
        Image.new("RGB", (300, 600), "black").save(buffer, "PNG")
        dvt = mock.MagicMock()
        dvt.__aenter__ = mock.AsyncMock(return_value=dvt)
        dvt.__aexit__ = mock.AsyncMock(return_value=False)
        shot = mock.MagicMock()
        shot.__aenter__ = mock.AsyncMock(return_value=shot)
        shot.__aexit__ = mock.AsyncMock(return_value=False)
        shot.get_screenshot = mock.AsyncMock(return_value=buffer.getvalue())
        self.session.rsd = object()
        with mock.patch.object(dvt_provider, "DvtProvider", return_value=dvt) as provider, \
                mock.patch.object(screenshot, "Screenshot", return_value=shot):
            for paused in (None, "call"):
                self.session.info.video_paused = paused
                png, width, height = await self.session.passcode_screen_png()
                self.assertEqual((width, height), (300, 600))
                self.assertEqual(Image.open(io.BytesIO(png)).size, (300, 600))
        self.assertEqual(shot.get_screenshot.await_count, 2)
        self.assertEqual(provider.call_args_list, [mock.call(self.session.rsd)] * 2)
        self.assertIsNone(self.session._wda)

    async def test_call_screenshot_keeps_fresh_capture_path_and_resize(self):
        self.session.info.video_paused = "call"
        self.session.passcode_screen_png = mock.AsyncMock(return_value=(b"new", 150, 300))
        self.assertEqual(await self.session.screen_png(300), (b"new", 150, 300))
        self.session.passcode_screen_png.assert_awaited_once_with(300)


if __name__ == "__main__":
    unittest.main()
