import asyncio
import contextlib
import types
import unittest
from unittest import mock

from pymobiledevice3.remote import userspace_tunnel

from utsushite.session import DeviceSession
from utsushite.tunnel import SessionTunnel


class SessionTunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        lifecycle_lock = asyncio.Lock()
        self.enterContext(mock.patch.object(
            userspace_tunnel, "_lifecycle_lock", return_value=lifecycle_lock))

    def make_tunnel(self, callback):
        tunnel = SessionTunnel(serial="fake-device", autopair=False,
                               on_transport_closed=callback)
        tunnel._exit_stack = contextlib.AsyncExitStack()
        return tunnel

    async def test_transport_event_precedes_media_teardown_and_session_joins_it(self):
        session = DeviceSession("fake-device", "wifi", lambda _info: None)
        session.info.state = "ready"
        session._open = mock.AsyncMock()
        order = []

        def transport_lost():
            order.append("transport_closed")
            session.lost("outer transport closed")

        tunnel = self.make_tunnel(transport_lost)
        transport_end = asyncio.Event()
        transport_waiting = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        session_joining = asyncio.Event()

        async def wait_transport():
            transport_waiting.set()
            await transport_end.wait()

        async def cleanup_media():
            order.append("media_teardown")
            session.lost("video receiver ended", OSError("Bad file descriptor"))
            cleanup_started.set()
            await cleanup_release.wait()
            order.append("transport_cleanup_finished")

        async def join_tunnel():
            session_joining.set()
            await tunnel.aclose()
            order.append("session_join_finished")

        client = types.SimpleNamespace(wait_closed=mock.AsyncMock(side_effect=wait_transport))
        tunnel._exit_stack.push_async_callback(cleanup_media)
        session._stack.push_async_callback(join_tunnel)
        watcher = tunnel._transport_watcher = asyncio.create_task(tunnel._watch_transport_closed(client))
        self.addAsyncCleanup(session.close)
        self.addCleanup(cleanup_release.set)
        await asyncio.wait_for(transport_waiting.wait(), 1)
        self.assertTrue(session.ready)
        self.assertEqual(order, [])
        with self.assertLogs(level="WARNING"):
            transport_end.set()
            await asyncio.wait_for(cleanup_started.wait(), 1)
            await asyncio.wait_for(session_joining.wait(), 1)
            self.assertEqual(session.info.error, "outer transport closed")
            self.assertFalse(session._closed.is_set())
            cleanup_release.set()
            await asyncio.wait_for(watcher, 1)
            await asyncio.wait_for(session.wait_closed(), 1)
        self.assertEqual(order, ["transport_closed", "media_teardown",
                                 "transport_cleanup_finished", "session_join_finished"])
        self.assertEqual(session.info.state, "failed")
        self.assertEqual(session.info.error, "outer transport closed")
        client.wait_closed.assert_awaited_once()
        session._open.assert_not_awaited()

    async def test_explicit_close_does_not_report_transport_failure(self):
        notify = mock.Mock()
        tunnel = self.make_tunnel(notify)
        waiting = asyncio.Event()
        released = asyncio.Event()

        async def wait_transport():
            waiting.set()
            await released.wait()

        cleanup = mock.AsyncMock()
        tunnel._exit_stack.push_async_callback(cleanup)
        client = types.SimpleNamespace(wait_closed=mock.AsyncMock(side_effect=wait_transport))
        watcher = tunnel._transport_watcher = asyncio.create_task(tunnel._watch_transport_closed(client))
        await asyncio.wait_for(waiting.wait(), 1)
        await tunnel.aclose()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        await tunnel.aclose()
        notify.assert_not_called()
        cleanup.assert_awaited_once()
        client.wait_closed.assert_awaited_once()

    async def test_cleanup_failure_does_not_repeat_transport_notification(self):
        notify = mock.Mock()
        tunnel = self.make_tunnel(notify)
        cleanup = mock.AsyncMock(side_effect=OSError("cleanup failed"))
        tunnel._exit_stack.push_async_callback(cleanup)
        client = types.SimpleNamespace(wait_closed=mock.AsyncMock())
        with self.assertLogs("pymobiledevice3.remote.userspace_tunnel", level="WARNING"):
            watcher = tunnel._transport_watcher = asyncio.create_task(tunnel._watch_transport_closed(client))
            await watcher
        await tunnel.aclose()
        notify.assert_called_once_with()
        cleanup.assert_awaited_once()
        client.wait_closed.assert_awaited_once()

    async def test_lost_keeps_first_cause_before_close_task_starts(self):
        session = DeviceSession("fake-device", "wifi", lambda _info: None)
        session.info.state = "ready"
        closing = asyncio.Event()
        session.close = mock.AsyncMock(side_effect=lambda **_kwargs: closing.set())
        with self.assertLogs("utsushite.session", level="WARNING") as captured:
            session.lost("outer transport closed")
            session.lost("video receiver ended", OSError("Bad file descriptor"))
            await asyncio.wait_for(closing.wait(), 1)
        self.assertEqual(session.info.error, "outer transport closed")
        self.assertEqual(len(captured.records), 1)
        session.close.assert_awaited_once_with(final_state="failed")


if __name__ == "__main__":
    unittest.main()
