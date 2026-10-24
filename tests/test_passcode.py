"""Manual XCTest entry: fresh AX labels, one secret command, no device unlock."""
import asyncio
import json
import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from ictrl import passcode
import test_passcode_automatic as automatic

SECRET = "975310"
CLOCK = [{"text": "10月17日土曜日", "x": 15, "y": 20, "w": 70, "h": 10},
         {"text": "5:12", "x": 20, "y": 35, "w": 60, "h": 25}]


def call_route(path: str, headers: dict, body: dict) -> tuple[int, dict]:
    from starlette.requests import Request
    from ictrl.server import create_app
    app = create_app(47800)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/passcode/{action}")
    raw = json.dumps(body).encode()
    scope = {"type": "http", "method": "POST", "path": path, "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "path_params": {"action": path.rsplit("/", 1)[-1]}}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    response = asyncio.run(route.endpoint(Request(scope, receive)))
    return response.status_code, json.loads(response.body)


class RouteTests(unittest.TestCase):
    def test_agent_requests_are_refused(self):
        with mock.patch.object(passcode, "enter") as enter, mock.patch.object(passcode, "save") as save:
            for action in ("enter", "save", "delete"):
                status, _ = call_route(f"/api/passcode/{action}", {"user-agent": "ictrl-mcp"},
                                       {"passcode": SECRET})
                self.assertEqual(status, 403, action)
            # A browser user agent without the iCtrl origin is refused too.
            status, _ = call_route("/api/passcode/enter", {"user-agent": "Mozilla/5.0"}, {})
            self.assertEqual(status, 403)
        enter.assert_not_called()
        save.assert_not_called()


class EnterTests(unittest.IsolatedAsyncioTestCase):
    patch = automatic.AutomaticTests.patch
    current_grant = automatic.AutomaticTests.current_grant
    cancel_task = automatic.AutomaticTests.cancel_task

    async def asyncSetUp(self):
        await automatic.AutomaticTests.asyncSetUp(self)

    async def test_manual_entry_works_without_hello(self):
        self.assertEqual(await passcode.enter(self.session), {"ok": True})
        self.assertEqual(self.session._stroke.await_count, len(passcode.strokes(SECRET)))
        self.assertTrue(self.current_grant()["blocked"])
        self.assertNotEqual(self.current_grant()["token"], self.grant["token"])

    async def test_nothing_is_typed_without_a_prompt(self):
        self.recognize.return_value = [{"text": "ordinary application"}]
        with self.assertRaises(passcode.PasscodeError):
            await passcode.enter(self.session)
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_device_lock_app_auth_and_clock_are_refused_even_with_legacy_grant(self):
        self.store[passcode.AUTO_TARGET] = json.dumps({**self.grant, "other_prompts": True})
        for paused in (None, "call"):
            for lines in [CLOCK, *automatic.NON_XCTEST_PROMPTS]:
                with self.subTest(paused=paused, lines=lines):
                    self.session.info.video_paused = paused
                    self.recognize.return_value = lines
                    with self.assertRaises(passcode.PasscodeError):
                        await passcode.enter(self.session)
        self.session._stroke.assert_not_awaited()
        self.session._ensure_keyboard.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))
        self.assertEqual(self.current_grant(), self.grant)

    async def test_legacy_confirmation_cannot_enable_a_bypass(self):
        for arguments in ({"confirmed_on_device": True}, {"open_lock_screen": True}):
            with self.subTest(arguments=arguments), self.assertRaises(TypeError):
                await passcode.enter(self.session, **arguments)
        self.read.assert_not_called()
        self.session._stroke.assert_not_awaited()

    async def test_typed_once_and_never_logged(self):
        self.session._stroke.side_effect = OSError(SECRET)
        with self.assertLogs("ictrl.passcode", logging.DEBUG) as logs:
            with self.assertRaises(passcode.PasscodeError) as raised:
                await passcode.enter(self.session)
        self.assertEqual(self.session._stroke.await_count, 1)  # no retry
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertFalse(any(SECRET in line for line in logs.output))


    async def test_recognized_prompt_uses_direct_keys_without_space_or_keyboard_registration(self):
        for paused in (None, "call"):
            with self.subTest(paused=paused):
                self.session.info.video_paused = paused
                self.session._stroke.reset_mock()
                self.session.screen_png.reset_mock()
                self.assertEqual(await passcode.enter(self.session), {"ok": True})
                self.assertEqual(self.session._stroke.await_args_list,
                                 [mock.call(usage, []) for usage, _ in passcode.strokes(SECRET)])
                self.session.screen_png.assert_not_awaited()
        self.session._ensure_keyboard.assert_not_awaited()
        self.assertTrue(self.current_grant()["blocked"])

    async def test_recognized_call_prompt_accepts_alphanumeric_code(self):
        self.session.info.video_paused = "call"
        self.store[passcode.TARGET] = "12aB"
        await passcode.enter(self.session)
        self.assertEqual(self.session._stroke.await_args_list,
                         [mock.call(usage, [0xE1] if shift else []) for usage, shift in passcode.strokes("12aB")])

    async def test_call_manual_entry_requires_xctest_ocr(self):
        self.session.info.video_paused = "call"
        self.recognize.return_value = [{"text": "ordinary application"}]
        with self.assertRaises(passcode.PasscodeError):
            await passcode.enter(self.session)
        self.session._stroke.assert_not_awaited()

    async def test_partial_field_is_refused_by_normal_manual_entry(self):
        self.recognize.return_value = automatic.PROMPT + [{"text": "●○○○○○"}]
        with self.assertRaises(passcode.PasscodeError):
            await passcode.enter(self.session)
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))


    async def test_call_ends_while_queued_no_secret_is_read(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def earlier(text):
            entered.set()
            await release.wait()

        self.session._type_text = mock.AsyncMock(side_effect=earlier)
        previous = asyncio.create_task(self.session.run({"op": "text", "text": "ordinary input"}))
        await entered.wait()
        self.session.info.video_paused = "call"
        attempt = asyncio.create_task(passcode.enter(self.session))
        await asyncio.sleep(0)
        self.session.info.video_paused = None
        release.set()
        await previous
        with self.assertRaises(passcode.PasscodeError):
            await attempt
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_video_state_transition_while_queued_refuses_before_secret_read(self):
        for before, after in ((None, "call"), ("call", None), (None, "protected")):
            with self.subTest(before=before, after=after):
                entered, release = asyncio.Event(), asyncio.Event()

                async def earlier():
                    entered.set()
                    await release.wait()

                self.session._release_all = mock.AsyncMock(side_effect=earlier)
                previous = asyncio.create_task(self.session.run({"op": "release"}))
                await entered.wait()
                self.session.info.video_paused = before
                generation = self.session.passcode_input_generation
                attempt = asyncio.create_task(passcode.enter(self.session))
                await asyncio.sleep(0)
                self.session.info.video_paused = after
                release.set()
                await previous
                with self.assertRaises(passcode.PasscodeError):
                    await attempt
                self.assertEqual(self.session.passcode_input_generation, generation)
                self.session._stroke.assert_not_awaited()
                self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_video_state_transition_during_send_stops_remaining_keys(self):
        for before, after in ((None, "call"), ("call", None), (None, "protected")):
            with self.subTest(before=before, after=after):
                self.session.info.video_paused = before
                self.session._stroke.reset_mock()

                async def stroke(usage, modifiers):
                    self.session.info.video_paused = after

                self.session._stroke.side_effect = stroke
                with self.assertRaises(passcode.PasscodeError):
                    await passcode.enter(self.session)
                self.assertEqual(self.session._stroke.await_args_list,
                                 [mock.call(passcode.strokes(SECRET)[0][0], [])])
                self.assertTrue(self.current_grant()["blocked"])

    async def test_copy_completion_invalidates_queued_call_entry(self):
        for op in ("plain_copy",):
            with self.subTest(op=op):
                entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
                self.session.info.video_paused = None
                initial_generation = self.session.passcode_input_generation
                real_submit = self.session.submit
                tasks = []

                async def earlier(*_args, **_kwargs):
                    entered.set()
                    await release.wait()
                    return {"ok": True}

                def submit(command):
                    future = real_submit(command)
                    if command.get("op") == "secret_strokes":
                        queued.set()
                    return future

                agent = SimpleNamespace(open_url_once=mock.AsyncMock(side_effect=earlier))
                with mock.patch.object(self.session, "_plain_copy", mock.AsyncMock(side_effect=earlier)) as legacy, \
                        mock.patch.object(self.session, "submit", side_effect=submit):
                    try:
                        previous = asyncio.create_task(self.session.run(
                            {"op": op, "agent": agent, "guard": lambda: True}))
                        tasks.append(previous)
                        await asyncio.wait_for(entered.wait(), 2)
                        self.session.info.video_paused = "call"
                        attempt = asyncio.create_task(passcode.enter(self.session))
                        tasks.append(attempt)
                        await asyncio.wait_for(queued.wait(), 2)
                        self.assertEqual(self.session.passcode_input_generation, initial_generation + 1)
                        self.assertFalse(attempt.done())
                        release.set()
                        self.assertEqual(await previous, {"ok": True})
                        with self.assertRaises(passcode.PasscodeError):
                            await attempt
                        self.assertEqual(self.session.passcode_input_generation, initial_generation + 2)
                        if op == "plain_copy":
                            legacy.assert_awaited_once()
                            agent.open_url_once.assert_not_awaited()
                        else:
                            agent.open_url_once.assert_awaited_once()
                            legacy.assert_not_awaited()
                    finally:
                        release.set()
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                self.session._stroke.assert_not_awaited()
                self.read.assert_not_called()
                self.write.assert_not_called()
                self.assertFalse(passcode._entering)
                self.assertEqual(self.current_grant(), self.grant)

    async def test_user_input_during_prepare_stops_before_secret_read(self):
        async def prompt(_png):
            self.session.passcode_input_generation += 1
            return automatic.PROMPT

        self.recognize.side_effect = prompt
        with self.assertRaises(passcode.PasscodeError):
            await passcode.enter(self.session)
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_call_input_competition_stops_remaining_keys_and_stays_blocked(self):
        self.session.info.video_paused = "call"

        async def stroke(usage, modifiers):
            self.session.passcode_input_generation += 1

        self.session._stroke.side_effect = stroke
        with self.assertRaises(passcode.PasscodeError):
            await passcode.enter(self.session)
        self.assertEqual(self.session._stroke.await_count, 1)
        self.assertTrue(self.current_grant()["blocked"])

    async def test_cancel_call_entry_joins_inflight_key_without_replay(self):
        self.session.info.video_paused = "call"
        entered, release = asyncio.Event(), asyncio.Event()

        async def stroke(usage, modifiers):
            entered.set()
            await release.wait()

        self.session._stroke.side_effect = stroke
        attempt = asyncio.create_task(passcode.enter(self.session))
        await entered.wait()
        attempt.cancel()
        await asyncio.sleep(0)
        self.assertFalse(attempt.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await attempt
        self.assertEqual(self.session._stroke.await_count, 1)
        self.assertFalse(passcode._entering)
        self.assertTrue(self.current_grant()["blocked"])

    async def test_call_manual_entry_does_not_create_automatic_permission(self):
        self.store.pop(passcode.AUTO_TARGET)
        self.session.info.video_paused = "call"
        await passcode.enter(self.session)
        self.assertIsNone(self.current_grant())
        self.write.assert_not_called()

    async def test_manual_input_invalidates_an_older_wda_confirmation(self):
        previous = passcode.XCTestAutoEntry(self.session)
        previous._grant = dict(self.grant)
        previous._consumed = {**self.grant, "blocked": True}
        previous._completed = True
        self.store[passcode.AUTO_TARGET] = json.dumps(previous._consumed)
        self.session.info.video_paused = "call"
        await passcode.enter(self.session)
        consumed = self.current_grant()
        previous.confirmed()
        self.assertEqual(self.current_grant(), consumed)
        self.assertTrue(consumed["blocked"])
        self.assertNotEqual(consumed["token"], previous._consumed["token"])

    def test_strokes_end_with_return(self):
        strokes = passcode.strokes("12aB")
        self.assertEqual(strokes[-1], (0x28, False))
        self.assertEqual(strokes[:4], [(0x1E, False), (0x1F, False), (0x04, False), (0x05, True)])


if __name__ == "__main__":
    unittest.main()
