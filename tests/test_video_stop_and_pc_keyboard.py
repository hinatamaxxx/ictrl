"""Stopping the video keeps the connection; unlinking the PC keyboard leaves
the on-screen keyboard to the user."""
import contextlib
import unittest
from unittest import mock

from utsushite.session import DeviceSession


class VideoStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_closes_only_the_video(self):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        closed = []
        stack = contextlib.AsyncExitStack()
        stack.push_async_callback(lambda: closed.append("video") or _none())
        session._video_stack = stack
        self.assertEqual(await session.stop_video(), {"ok": True})
        self.assertEqual(closed, ["video"])
        self.assertEqual(session.info.video_paused, "off")
        self.assertEqual((await session.stop_video())["video"], "already off")


async def _none():
    return None


class PcKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlinked_keyboard_is_not_reconnected_by_touches(self):
        session = DeviceSession("test-phone", "usb", lambda _info: None)
        session._show_phone_keyboard = mock.AsyncMock(return_value={"ok": True})
        session._before_typing = mock.AsyncMock()
        await session._execute({"op": "pc_keyboard", "on": False})
        session._show_phone_keyboard.assert_awaited_once()
        await session._hide_phone_keyboard()
        session._before_typing.assert_not_awaited()
        await session._execute({"op": "pc_keyboard", "on": True})
        session._before_typing.assert_awaited_once()       # linked again: connected at once
        await session._execute({"op": "pc_keyboard", "on": True})
        session._before_typing.assert_awaited_once()       # already linked: nothing


if __name__ == "__main__":
    unittest.main()
