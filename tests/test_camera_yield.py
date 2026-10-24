"""The screen video gives way to the camera (iOS does not run both)."""
import asyncio
import unittest
from unittest import mock

from ictrl import physical
from ictrl.session import DeviceSession

IN_USE = ("[Recording Indicator] Active camera/microphone activity changed:\n{(\n"
          "    <SBSensorActivityAttribution: 0x1; displayName: カメラ; bundleIdentifier: com.apple.camera; "
          "website-non-nil: NO; isSystemService: NO; sensor: camera>\n)}")
NOTHING = "[Recording Indicator] Active camera/microphone activity changed:\n{(\n)}"
MIC_ONLY = IN_USE.replace("sensor: camera>", "sensor: microphone>")


class CameraYieldTests(unittest.IsolatedAsyncioTestCase):
    async def test_springboard_lines(self):
        seen = []
        watcher = physical.PhysicalTouchWatcher(None, mock.AsyncMock(), on_camera=seen.append)
        for message in (IN_USE, MIC_ONLY, NOTHING):
            self.assertTrue(message.startswith(physical._SENSORS_CHANGED))
            watcher.on_camera("sensor: camera>" in message)
        self.assertEqual(seen, [True, False, False])

    async def test_video_stops_for_the_camera_and_comes_back(self):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        session._video_stack = object()

        async def stop(reason="off"):
            session._video_stack = None
            session.info.video_paused = reason
            return {"ok": True}

        session.stop_video = mock.AsyncMock(side_effect=stop)
        session.start_video = mock.AsyncMock(return_value={"ok": True})
        session._camera_in_use(True)
        await asyncio.sleep(0)
        session.stop_video.assert_awaited_once_with("camera")
        session._camera_in_use(True)                 # no change, nothing again
        session._camera_in_use(False)
        await asyncio.sleep(0)
        session.start_video.assert_awaited_once()

    async def test_video_stopped_by_the_user_stays_off(self):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        session.info.video_paused = "off"
        session.start_video = mock.AsyncMock()
        session._camera_in_use(True)
        session._camera_in_use(False)
        await asyncio.sleep(0)
        session.start_video.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
