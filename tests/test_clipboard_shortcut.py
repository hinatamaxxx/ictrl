import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from starlette.requests import Request

from ictrl import manager, server
from ictrl.session import DeviceSession, SessionError


class ShortcutSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(manager, "DATA_DIR", Path(directory)))
        self.manager = manager.Manager()
        self.manager.notify = mock.Mock()
        self.session = types.SimpleNamespace(set_clipboard_shortcut_mode=mock.AsyncMock(),
                                             set_clipboard_watch=mock.AsyncMock())
        self.manager.session = self.session

    async def test_keyboard_is_the_only_mode(self):
        self.assertEqual(self.manager.settings["clipboard_shortcut_mode"], "keyboard")
        with self.assertRaises(ValueError):
            await self.manager.update_settings({"clipboard_shortcut_mode": "url"})
        self.session.set_clipboard_shortcut_mode.assert_not_awaited()

    async def test_a_saved_url_mode_becomes_keyboard(self):
        self.manager._settings_path.write_text(json.dumps({"clipboard_shortcut_mode": "url"}), "utf-8")
        self.assertEqual(manager.Manager().settings["clipboard_shortcut_mode"], "keyboard")

    async def test_invalid_mode_cannot_change_other_settings_or_persist(self):
        original = dict(self.manager.settings)
        for value in (None, True, 1, [], {}, "url", "URL", "other"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.manager.update_settings({"clipboard_shortcut_mode": value,
                                                    "auto_copy_to_pc": False})
        self.assertEqual(self.manager.settings, original)
        self.assertFalse(self.manager._settings_path.exists())
        self.manager.notify.assert_not_called()
        self.session.set_clipboard_watch.assert_not_awaited()
        self.session.set_clipboard_shortcut_mode.assert_not_awaited()

    async def test_routes_expose_mode_and_reject_invalid_before_mutation(self):
        with mock.patch.object(server, "Manager", return_value=self.manager):
            app = server.create_app(47800)

        async def request(path, method="GET", body=None):
            async def receive():
                return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
            req = Request({"type": "http", "method": method, "path": path, "query_string": b"",
                           "headers": [(b"user-agent", b"Mozilla/5.0")], "path_params": {}}, receive)
            route = next(r for r in app.routes if getattr(r, "path", None) == path)
            return await route.endpoint(req)

        response = await request("/api/clipboard/history")
        self.assertEqual(json.loads(response.body)["clipboard_shortcut_mode"], "keyboard")
        response = await request("/api/settings", "POST", {"clipboard_shortcut_mode": [],
                                                           "auto_copy_to_pc": False})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.manager.settings["auto_copy_to_pc"])
        self.assertEqual(self.manager.settings["clipboard_shortcut_mode"], "keyboard")


class ShortcutDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = DeviceSession("fake-device", "wifi", lambda _info: None)
        self.session.info.state = "ready"
        self.session._plain_copy = mock.AsyncMock(return_value={"ok": True})
        self.session._release_all = mock.AsyncMock()
        self.agent = types.SimpleNamespace(ensure=mock.AsyncMock(),
                                           open_url_once=mock.AsyncMock(return_value={"ok": True}))
        self.session.wda = mock.Mock(return_value=self.agent)
        self.addAsyncCleanup(self.session.close)

    def start_worker(self):
        self.session._input_task = asyncio.create_task(self.session._input_worker())
        self.session._stack.push_async_callback(self.session._stop_input)

    async def test_keyboard_default_does_not_start_wda(self):
        self.start_worker()
        self.assertEqual(await self.session.trigger_plain_copy(), {"ok": True, "mode": "keyboard"})
        self.session._plain_copy.assert_awaited_once()
        self.session.wda.assert_not_called()

    async def test_concurrent_requests_share_one_trigger(self):
        self.start_worker()
        started, release = asyncio.Event(), asyncio.Event()

        async def copy():
            started.set()
            await release.wait()
            return {"ok": True}

        self.session._plain_copy.side_effect = copy
        first = asyncio.create_task(self.session.trigger_plain_copy())
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.session.trigger_plain_copy())
        await asyncio.sleep(0)  # schedule the second waiter before resolving readiness
        release.set()
        results = await asyncio.gather(first, second)
        self.assertEqual(results, [{"ok": True, "mode": "keyboard"}] * 2)
        self.session._plain_copy.assert_awaited_once()

    async def test_cancelled_queued_request_never_reconnects_keyboard(self):
        queued = asyncio.Event()
        submit = self.session.submit

        def capture(command):
            future = submit(command)
            queued.set()
            return future

        self.session.submit = capture
        task = asyncio.create_task(self.session.trigger_plain_copy())
        await asyncio.wait_for(queued.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.start_worker()
        await self.session.run({"op": "release"})
        self.session._plain_copy.assert_not_awaited()

    async def test_cancelling_one_waiter_does_not_cancel_another_request(self):
        self.start_worker()
        started, release = asyncio.Event(), asyncio.Event()

        async def copy():
            started.set()
            await release.wait()
            return {"ok": True}

        self.session._plain_copy.side_effect = copy
        first = asyncio.create_task(self.session.trigger_plain_copy())
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.session.trigger_plain_copy())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual(await second, {"ok": True, "mode": "keyboard"})
        self.session._plain_copy.assert_awaited_once()

    async def test_automatic_off_skips_trigger_but_explicit_manual_request_works(self):
        self.start_worker()
        self.session.clipboard_auto_enabled = False
        result = await self.session.trigger_plain_copy(automatic=True)
        self.assertIn("skipped", result)
        self.session._plain_copy.assert_not_awaited()
        self.session.wda.assert_not_called()
        self.assertEqual(await self.session.trigger_plain_copy(), {"ok": True, "mode": "keyboard"})

    async def test_invalid_mode_does_not_trigger(self):
        with self.assertRaises(ValueError):
            await self.session.set_clipboard_shortcut_mode("url")
        self.assertEqual(self.session.clipboard_shortcut_mode, "keyboard")
        self.session.wda.assert_not_called()
        self.assertTrue(self.session._input.empty())


if __name__ == "__main__":
    unittest.main()
