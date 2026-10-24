"""A prepared WDA runner may open the clipboard shortcut at most once."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from ictrl import wda


URL = "shortcuts://run-shortcut?name=ictrlcopy"


def screen(contents=""):
    return ('<XCUIElementTypeApplication width="402" height="874">'
            f"{contents}</XCUIElementTypeApplication>")


class ShortcutUrlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # No RSD connection exists: every request is intercepted below.
        self.agent = wda.WdaAgent(SimpleNamespace())
        self.runner = asyncio.get_running_loop().create_future()
        self.agent._task = self.runner
        self.agent.sid = "prepared-session"
        self.agent._ready = True
        self.allowed = True
        self.locked = {"value": False}
        self.source = {"value": screen('<XCUIElementTypeButton label="Copy"/>')}
        self.post_error = None
        self.source_started = asyncio.Event()
        self.source_release = None
        self.operations = []
        self.agent.request = mock.AsyncMock(side_effect=self.request)
        self.agent.ensure = mock.AsyncMock()
        self.agent._configure = mock.AsyncMock()
        self.agent.session_request = mock.AsyncMock()
        self.agent.elements = mock.AsyncMock()

    async def asyncTearDown(self):
        for task in self.operations:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.operations, return_exceptions=True)
        self.runner.cancel()
        # These helpers can start/reconfigure/retry and are never appropriate
        # while this single action owns the input worker.
        self.agent.ensure.assert_not_awaited()
        self.agent._configure.assert_not_awaited()
        self.agent.session_request.assert_not_awaited()
        self.agent.elements.assert_not_awaited()

    async def request(self, method, path, body=None, **_kwargs):
        if (method, path) == ("GET", "/wda/locked"):
            return self.locked
        if (method, path) == ("GET", wda.SOURCE_PATH):
            self.source_started.set()
            if self.source_release is not None:
                await self.source_release.wait()
            return self.source
        if (method, path) == ("POST", "/session/prepared-session/url"):
            if self.post_error is not None:
                raise self.post_error
            return {"value": None}
        raise AssertionError(f"unexpected WDA request: {method} {path}")

    async def open(self):
        return await self.agent.open_url_once(URL, guard=lambda: self.allowed)

    def assert_no_post(self):
        self.assertFalse([call for call in self.agent.request.await_args_list
                          if call.args[0] == "POST"])

    async def change_during_source(self, change):
        self.source_release = asyncio.Event()
        task = asyncio.create_task(self.open())
        self.operations.append(task)
        await asyncio.wait_for(self.source_started.wait(), 2)
        change()
        self.source_release.set()
        with self.assertRaises(wda.WdaError):
            await task
        self.assert_no_post()

    async def test_unlocked_screen_opens_url_exactly_once(self):
        self.assertEqual(await self.open(), {"ok": True})
        self.assertEqual(self.agent.request.await_args_list, [
            mock.call("GET", "/wda/locked"),
            mock.call("GET", wda.SOURCE_PATH, timeout=60),
            mock.call("POST", "/session/prepared-session/url", {"url": URL}),
        ])

    async def test_locked_or_unknown_state_never_opens_url(self):
        for state in ({"value": True}, {}, {"value": None}, {"value": 0},
                      {"value": "false"}, {"value": ""}):
            with self.subTest(state=state):
                self.locked = state
                self.agent.request.reset_mock()
                with self.assertRaises(wda.WdaError):
                    await self.open()
                self.agent.request.assert_awaited_once_with("GET", "/wda/locked")

    async def test_illuminated_lock_screen_blocks_even_when_locked_is_false(self):
        self.source = {"value": screen(
            '<XCUIElementTypeImage name="proud-lock" x="180" y="40" width="30" height="30"/>')}
        with self.assertRaises(wda.WdaError):
            await self.open()
        self.assertEqual(self.agent.request.await_count, 2)
        self.assert_no_post()

    async def test_partly_entered_passcode_field_blocks_url(self):
        for attribute in ('label="パスコード"', 'name="Enter Passcode"'):
            with self.subTest(attribute=attribute):
                self.source = {"value": screen(
                    f'<XCUIElementTypeSecureTextField {attribute} value="••"/>')}
                self.agent.request.reset_mock()
                with self.assertRaises(wda.WdaError):
                    await self.open()
                self.assert_no_post()

    async def test_offscreen_cover_sheet_does_not_block_unlocked_screen(self):
        self.source = {"value": screen(
            '<XCUIElementTypeImage name="proud-lock" x="180" y="-834" width="30" height="30"/>')}
        self.assertEqual(await self.open(), {"ok": True})
        self.agent.request.assert_awaited_with(
            "POST", "/session/prepared-session/url", {"url": URL})

    async def test_unreadable_screen_never_opens_url(self):
        for source in ({}, {"value": None}, {"value": "<broken"},
                       {"value": '<XCUIElementTypeApplication/>'},
                       {"value": '<XCUIElementTypeApplication width="0" height="874"/>'},
                       {"value": '<XCUIElementTypeApplication width="402" height="nan"/>'},
                       {"value": '<XCUIElementTypeApplication width="inf" height="874"/>'}):
            with self.subTest(source=source):
                self.source = source
                self.agent.request.reset_mock()
                with self.assertRaises(wda.WdaError):
                    await self.open()
                self.assert_no_post()

    async def test_cancelled_guard_before_start_sends_no_requests(self):
        self.allowed = False
        with self.assertRaises(wda.WdaError):
            await self.open()
        self.agent.request.assert_not_awaited()

    async def test_unprepared_runner_is_rejected_without_starting_it(self):
        for field, value in (("_ready", False), ("_task", None), ("sid", None)):
            with self.subTest(field=field):
                previous = getattr(self.agent, field)
                setattr(self.agent, field, value)
                try:
                    with self.assertRaises(wda.WdaError):
                        await self.open()
                    self.agent.request.assert_not_awaited()
                finally:
                    setattr(self.agent, field, previous)

    async def test_cancelled_guard_during_source_prevents_post(self):
        await self.change_during_source(lambda: setattr(self, "allowed", False))

    async def test_session_change_during_source_prevents_post(self):
        await self.change_during_source(lambda: setattr(self.agent, "sid", "replacement-session"))

    async def test_runner_replacement_during_source_prevents_post(self):
        replacement = asyncio.get_running_loop().create_future()
        try:
            await self.change_during_source(lambda: setattr(self.agent, "_task", replacement))
        finally:
            replacement.cancel()

    async def test_runner_exit_during_source_prevents_post(self):
        await self.change_during_source(lambda: self.runner.set_result(None))

    async def test_readiness_loss_during_source_prevents_post(self):
        await self.change_during_source(lambda: setattr(self.agent, "_ready", False))

    async def test_task_cancelled_during_source_never_posts(self):
        self.source_release = asyncio.Event()
        task = asyncio.create_task(self.open())
        self.operations.append(task)
        await asyncio.wait_for(self.source_started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_no_post()

    async def test_uncertain_post_result_is_never_replayed(self):
        for error in (wda.WdaError("invalid session id"), ConnectionError("connection lost"),
                      asyncio.TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.post_error = error
                self.agent.request.reset_mock()
                with self.assertRaises(type(error)) as caught:
                    await self.open()
                self.assertIs(caught.exception, error)
                posts = [call for call in self.agent.request.await_args_list if call.args[0] == "POST"]
                self.assertEqual(posts, [
                    mock.call("POST", "/session/prepared-session/url", {"url": URL}),
                ])
                self.assertEqual(self.agent.request.await_count, 3)


if __name__ == "__main__":
    unittest.main()
