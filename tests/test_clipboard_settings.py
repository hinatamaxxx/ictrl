import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from starlette.requests import Request

from ictrl import clipboard, manager, server
from ictrl.session import DeviceSession


class ClipboardSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(manager, "DATA_DIR", Path(directory)))
        self.manager = manager.Manager()
        self.manager.notify = mock.Mock()
        self.session = types.SimpleNamespace(set_clipboard_watch=mock.AsyncMock())
        self.manager.session = self.session

    async def test_disabling_waits_for_monitor_stop_before_reply_and_notification(self):
        started, released = asyncio.Event(), asyncio.Event()

        async def stop(enabled):
            self.assertFalse(enabled)
            started.set()
            await released.wait()

        self.session.set_clipboard_watch.side_effect = stop
        task = asyncio.create_task(self.manager.update_settings({"auto_copy_to_pc": False}))
        await asyncio.wait_for(started.wait(), 1)
        self.assertFalse(task.done())
        self.assertFalse(self.manager.settings["auto_copy_to_pc"])
        self.manager.notify.assert_not_called()
        released.set()
        result = await task
        self.assertFalse(result["auto_copy_to_pc"])
        self.session.set_clipboard_watch.assert_awaited_once_with(False)
        self.manager.notify.assert_called_once()

    async def test_enabling_starts_watch_on_existing_session(self):
        self.manager.settings["auto_copy_to_pc"] = False
        await self.manager.update_settings({"auto_copy_to_pc": True})
        self.session.set_clipboard_watch.assert_awaited_once_with(True)

    async def test_pc_to_iphone_setting_does_not_restart_phone_watch(self):
        await self.manager.update_settings({"auto_copy_to_iphone": False})
        self.session.set_clipboard_watch.assert_not_awaited()

    async def test_disabling_without_device_is_persisted(self):
        self.manager.session = None
        await self.manager.update_settings({"auto_copy_to_pc": False})
        saved = json.loads(self.manager._settings_path.read_text("utf-8"))
        self.assertFalse(saved["auto_copy_to_pc"])

    async def test_connect_applies_saved_setting_before_open(self):
        self.manager.session = None
        self.manager.settings["auto_copy_to_pc"] = False
        self.manager.settings["clipboard_shortcut_mode"] = "url"
        self.manager.status = mock.Mock(return_value={"state": "ready"})
        self.manager._changed = mock.Mock()
        fake_session = types.SimpleNamespace(info=types.SimpleNamespace(state="idle"), volume=None)

        async def open_session():
            self.assertIs(fake_session.clipboard_auto_enabled, False)
            self.assertEqual(fake_session.clipboard_shortcut_mode, "url")
            fake_session.info.state = "ready"

        fake_session.open = mock.AsyncMock(side_effect=open_session)
        with mock.patch.object(manager, "DeviceSession", return_value=fake_session):
            result = await self.manager.connect("fake-device", "wifi")
        self.assertEqual(result, {"state": "ready"})
        fake_session.open.assert_awaited_once()

    async def test_disabled_drops_late_notification_without_processing(self):
        self.manager.settings["auto_copy_to_pc"] = False
        with mock.patch.object(self.manager, "_handle_iphone_copy", new_callable=mock.AsyncMock) as process:
            self.manager._iphone_copied(object())
            process.assert_not_called()

    async def test_disabled_drops_already_queued_copy_without_reading_content(self):
        self.manager.settings["auto_copy_to_pc"] = False
        with mock.patch("ictrl.clipboard.content_parts") as parts:
            await self.manager._handle_iphone_copy(object())
            parts.assert_not_called()
        self.assertEqual(self.manager.clips.items, [])
        self.manager.notify.assert_not_called()

    async def test_disable_during_processing_does_not_publish_or_copy_result(self):
        started, released = asyncio.Event(), asyncio.Event()

        async def conversion(_function, _content):
            started.set()
            await released.wait()
            return "example", None

        with mock.patch("ictrl.manager.asyncio.to_thread", side_effect=conversion), \
                mock.patch.object(self.manager, "copy_item_to_pc") as copy:
            task = asyncio.create_task(self.manager._handle_iphone_copy(object()))
            await asyncio.wait_for(started.wait(), 1)
            await self.manager.update_settings({"auto_copy_to_pc": False})
            released.set()
            await task
            copy.assert_not_called()
        self.assertEqual(self.manager.clips.items, [])
        self.assertEqual([call.args[0]["t"] for call in self.manager.notify.call_args_list], ["settings"])

    async def test_settings_route_awaits_watch_shutdown(self):
        with mock.patch.object(server, "Manager", return_value=self.manager):
            app = server.create_app(47800)
        route = next(r for r in app.routes if getattr(r, "path", None) == "/api/settings")
        body = json.dumps({"auto_copy_to_pc": False}).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/api/settings", "query_string": b"",
                           "headers": [(b"user-agent", b"Mozilla/5.0"),
                                       (b"origin", b"http://127.0.0.1:47800")], "path_params": {}}, receive)
        response = await route.endpoint(request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.body)["auto_copy_to_pc"])
        self.session.set_clipboard_watch.assert_awaited_once_with(False)


class SessionClipboardSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = DeviceSession("fake-device", "wifi", lambda _info: None)
        self.session.info.state = "ready"
        self.monitor = types.SimpleNamespace(start=mock.AsyncMock(), stop=mock.AsyncMock())
        self.factory = self.enterContext(mock.patch.object(
            clipboard, "BoundedPasteboardMonitor", return_value=self.monitor))
        self.enterContext(mock.patch.object(clipboard, "PLAIN_COPY_TRIGGER", [None]))
        self.enterContext(mock.patch.object(clipboard, "PLAIN_COPY_FAILED", [None]))
        self.enterContext(mock.patch.object(clipboard, "PLAIN_COPY_PENDING", [None]))
        self.addAsyncCleanup(self.session.close)

    async def test_disabled_watch_does_not_open_pasteboard_services(self):
        self.session.clipboard_auto_enabled = False
        await self.session._watch_clipboard()
        self.factory.assert_not_called()
        self.assertEqual(self.session.clipboard_watch, "off")

    async def test_disable_stops_monitor_and_clears_shortcut_callbacks(self):
        await self.session.set_clipboard_watch(True)
        self.assertEqual(self.session.clipboard_watch, "on")
        self.assertIsNotNone(clipboard.PLAIN_COPY_TRIGGER[0])
        await self.session.set_clipboard_watch(False)
        self.monitor.stop.assert_awaited_once()
        self.assertIsNone(self.session._pasteboard_monitor)
        self.assertEqual(self.session.clipboard_watch, "off")
        self.assertIsNone(clipboard.PLAIN_COPY_TRIGGER[0])
        self.assertIsNone(clipboard.PLAIN_COPY_FAILED[0])
        self.assertIsNone(clipboard.PLAIN_COPY_PENDING[0])
        self.assertTrue(self.session.ready)
        await self.session.set_clipboard_watch(False)
        self.monitor.stop.assert_awaited_once()

    async def test_enabling_twice_does_not_duplicate_monitor(self):
        await self.session.set_clipboard_watch(True)
        await self.session.set_clipboard_watch(True)
        self.factory.assert_called_once()
        self.monitor.start.assert_awaited_once()

    async def test_cancelled_queued_shortcut_does_not_reconnect_keyboard(self):
        self.session._plain_copy = mock.AsyncMock()
        future = self.session.submit({"op": "plain_copy"})
        future.cancel()
        self.session._input_task = asyncio.create_task(self.session._input_worker())
        self.session._stack.push_async_callback(self.session._stop_input)
        # A following non-device command marks completion of the canceled item.
        self.session._execute = mock.AsyncMock(return_value={"ok": True})
        await self.session.run({"op": "test-marker"})
        self.assertEqual(self.session._execute.await_count, 1)
        self.session._plain_copy.assert_not_awaited()

    async def test_shutdown_joins_in_progress_monitor_stop_before_unwinding_transport(self):
        started, stopping, released, closing = (asyncio.Event() for _ in range(4))
        order = []
        # Use the library's real stop(): it clears _task before awaiting its
        # canceled receiver, so a second direct stop() would return early.
        monitor = clipboard.PasteboardMonitor(None, lambda _content: None)

        async def receiver():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopping.set()
                await released.wait()
                order.append("monitor_closed")

        monitor._run = receiver
        self.factory.return_value = monitor
        self.session._on_change = lambda info: closing.set() if info.state == "closing" else None
        await self.session.set_clipboard_watch(True)
        await asyncio.wait_for(started.wait(), 1)
        self.session._stack.callback(lambda: order.append("transport_teardown"))
        disable = asyncio.create_task(self.session.set_clipboard_watch(False))
        shutdown = None
        try:
            await asyncio.wait_for(stopping.wait(), 1)
            self.assertIsNone(monitor._task)
            shutdown = asyncio.create_task(self.session.close())
            await asyncio.wait_for(closing.wait(), 1)
            self.assertFalse(shutdown.done())
            self.assertFalse(disable.done())
            self.assertNotIn("transport_teardown", order)
        finally:
            released.set()
            await asyncio.wait_for(disable, 1)
            if shutdown is not None:
                await asyncio.wait_for(shutdown, 1)
        self.assertEqual(order, ["monitor_closed", "transport_teardown"])
        self.assertEqual(self.session.info.state, "closed")

    async def test_watch_cannot_restart_after_shutdown_has_begun(self):
        self.session._closing = True
        self.session.info.state = "closing"
        try:
            await self.session.set_clipboard_watch(True)
            self.factory.assert_not_called()
        finally:
            # Allow this test's ordinary cleanup to close its fake session.
            self.session._closing = False


if __name__ == "__main__":
    unittest.main()
