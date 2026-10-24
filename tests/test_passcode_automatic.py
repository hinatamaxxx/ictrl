"""XCTest startup entry: real input queue, mocked AX labels, secrets and HID."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from ictrl import passcode
from ictrl.session import DeviceSession, SessionError
from ictrl.video import StreamConfig


SECRET = "975310"
DEVICE = "test-iphone"
PROMPT = [{"text": '"XCTest" を使うために', "x": 20, "y": 35, "w": 60, "h": 10},
          {"text": "iPhone のパスコードを入力", "x": 15, "y": 48, "w": 70, "h": 10},
          {"text": "キャンセル", "x": 5, "y": 10, "w": 20, "h": 8}]
LOCK_SCREEN = [{"text": "iPhone のパスコードを入力"}]
NON_XCTEST_PROMPTS = [
    [{"text": "パスコードを入力", "x": 15, "y": 35, "w": 70, "h": 10},
     {"text": "緊急", "x": 5, "y": 170, "w": 20, "h": 8},
     {"text": "キャンセル", "x": 65, "y": 170, "w": 30, "h": 8}],
    [{"text": "iPhoneのパスコードを入力", "x": 10, "y": 35, "w": 80, "h": 10},
     {"text": "キャンセル", "x": 5, "y": 10, "w": 30, "h": 8}],
    [{"text": "iPhoneのバスコードを入力", "x": 10, "y": 35, "w": 80, "h": 10},
     {"text": "Xのロックを解除するには認証してください", "x": 10, "y": 50, "w": 80, "h": 10}],
    [{"text": "Enter PIN", "x": 15, "y": 35, "w": 70, "h": 10},
     {"text": "Cancel", "x": 5, "y": 10, "w": 30, "h": 8}],
]


class PromptTests(unittest.TestCase):
    def test_partially_entered_masked_passcodes_are_refused(self):
        for text in ("●○○○○○", "●", "••••", "**○○", "入力済み: ● ○ ○ ○ ○ ○", "2○○○○○"):
            with self.subTest(text=text):
                self.assertTrue(passcode.has_entered_passcode([{"text": text}]))
                self.assertFalse(passcode.is_xctest_prompt(PROMPT + [{"text": text}]))
        self.assertFalse(passcode.has_entered_passcode([{"text": "○○○○○○"}]))
        self.assertFalse(passcode.has_entered_passcode([{"text": "1 2 3"}]))

    def test_requires_xctest_purpose_and_entry_instruction(self):
        for lines in (PROMPT,
                      [{"text": 'Enter your iPhone passcode to use "XCTest"'}, {"text": "Cancel"}],
                      [{"text": 'To enable “XCTest”, enter your iPhone passcode.'}, {"text": "Cancel"}],
                      [{"text": '"XCTest" を使用するために iPhoneのバスコードを入力'}, {"text": "キャンセル"}],
                      [{"text": "「XCTest」を使うために"}, {"text": "iPhoneの八スコ-ドを入力"},
                       {"text": "キャンセル"}],
                      [{"text": "Enter your iPhone"}, {"text": "passcode to use"},
                       {"text": '"XCTest"'}, {"text": "Cancel"}],
                      [{"text": "iPhoneのパスコードを入力"}, {"text": '「XCTest」を使うために'},
                       {"text": "キャンセル"}]):
            with self.subTest(lines=lines):
                self.assertTrue(passcode.is_xctest_prompt(lines))
        for lines in (LOCK_SCREEN, [{"text": "XCTest is running"}],
                      [{"text": "XCTest passcode help"}],
                      PROMPT + [{"text": "パスコードが間違っています"}],
                      PROMPT + [{"text": "iPhone は使用できません"}],
                      [{"text": 'Enter passcode to use XCTest. Incorrect passcode. Try again.'}]):
            with self.subTest(lines=lines):
                self.assertFalse(passcode.is_xctest_prompt(lines))

    def test_description_pages_and_quoted_instructions_are_not_prompts(self):
        for text in ('XCTest guide: Enter your iPhone passcode to use XCTest.',
                     'The message "Enter your iPhone passcode to use XCTest" may appear.',
                     '「XCTestを使うためにiPhoneのパスコードを入力」と表示されたら続行します。',
                     'XCTestを使うためにiPhoneのパスコードを入力する必要があります。',
                     '“Enter your iPhone passcode to use XCTest”'):
            with self.subTest(text=text):
                self.assertFalse(passcode.is_xctest_prompt([{"text": text}, {"text": "Cancel"}]))
        self.assertFalse(passcode.is_xctest_prompt([
            {"text": "XCTestを使うために"}, {"text": "設定画面の説明"},
            {"text": "iPhoneのパスコードを入力"}, {"text": "キャンセル"}]))

    def test_heading_alone_does_not_authorize_entry(self):
        self.assertFalse(passcode.is_xctest_prompt(PROMPT[:2]))
        self.assertFalse(passcode.is_xctest_prompt(PROMPT[:2] + [{"text": "キャンセルしてください"}]))
        self.assertFalse(passcode.is_xctest_prompt([
            {"text": 'Enter passcode to use XCTest Cancel'}]))

    def test_live_ios27_prompt_with_close_icon_and_reversed_ocr_order(self):
        # Text/boxes from Windows OCR on the actual XCTest prompt. There is
        # a close icon, no Cancel text, and the entry line is returned first.
        lines = [
            {"text": "iPhoneのバスコードを入力", "x": 112, "y": 687, "w": 977, "h": 73},
            {"text": '"XCTest"を使うために', "x": 191, "y": 584, "w": 830, "h": 75},
            {"text": "Enable UI Automation", "x": 218, "y": 795, "w": 770, "h": 61},
        ]
        self.assertTrue(passcode.is_xctest_prompt(lines, (1206, 2622)))
        self.assertFalse(passcode.is_xctest_prompt(lines[:2], (1206, 2622)))
        self.assertFalse(passcode.is_xctest_prompt([
            *lines[:2], {**lines[2], "y": 1800}], (1206, 2622)))
        self.assertFalse(passcode.is_xctest_prompt([
            *lines[:2], {**lines[2], "text": "Enable UI Automation guide"}], (1206, 2622)))

    def test_heading_geometry_is_relative_visible_and_adjacent(self):
        self.assertTrue(passcode.is_xctest_prompt(PROMPT, (100, 200)))
        scaled = [{**row, **{k: row[k] * 12 for k in ("x", "y", "w", "h")}}
                  for row in PROMPT]
        self.assertTrue(passcode.is_xctest_prompt(scaled, (1200, 2400)))
        self.assertTrue(passcode.is_xctest_prompt(
            [{**row, "x": row["x"] + 10, "y": row["y"] + 120} for row in PROMPT], (100, 200)))
        for invalid in (
                [PROMPT[0], {**PROMPT[1], "y": 110}, PROMPT[2]],
                PROMPT[:2] + [{**PROMPT[2], "x": -5}],
                [{"text": row["text"]} for row in PROMPT],
                [{**PROMPT[0], "h": float("nan")}, *PROMPT[1:]]):
            with self.subTest(invalid=invalid):
                self.assertFalse(passcode.is_xctest_prompt(invalid, (100, 200)))

    def test_subtitle_prompt_can_move_and_scale_with_the_ios_layout(self):
        lines = [*PROMPT[:2], {"text": "Enable UI Automation", "x": 20, "y": 65, "w": 60, "h": 10}]
        for dx, dy, scale in ((0, 0, 1), (70, 180, 1), (10, 120, 6)):
            moved = [{**line, "x": (line["x"] + dx) * scale, "y": (line["y"] + dy) * scale,
                      "w": line["w"] * scale, "h": line["h"] * scale} for line in lines]
            with self.subTest(dx=dx, dy=dy, scale=scale):
                self.assertTrue(passcode.is_xctest_prompt(moved, (200 * scale, 400 * scale)))
        self.assertFalse(passcode.is_xctest_prompt(
            [*lines[:2], {**lines[2], "x": 105}], (200, 200)))


class AutomaticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Every credential operation is intercepted, including status reads.
        # These tests must never inspect the developer's actual passcode.
        self.store = {passcode.TARGET: SECRET}
        self.grant = {"udid": DEVICE, "token": "test-grant", "blocked": False}
        self.store[passcode.AUTO_TARGET] = json.dumps(self.grant)
        self.write_events = []

        def write(target, value):
            self.write_events.append((target, value))
            self.store[target] = value

        self.read = self.patch("_read_credential", mock.Mock(side_effect=self.store.get))
        self.write = self.patch("_write_credential", mock.Mock(side_effect=write))
        self.delete = self.patch("_delete_credential", mock.Mock(
            side_effect=lambda target: self.store.pop(target, None)))
        self.patch("_entering", False)
        self.recognize = mock.AsyncMock(return_value=PROMPT)
        async def observed(session):
            return passcode.is_xctest_prompt(await self.recognize(b"ax-labels"))
        patcher = mock.patch.object(passcode, "shows_prompt", side_effect=observed)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.session = DeviceSession(DEVICE, "usb", lambda info: None)
        self.session.info.state = "ready"
        self.session.passcode_input_generation = 0
        self.session.screen_png = mock.AsyncMock(return_value=(b"prompt-png", 100, 200))
        self.session.passcode_screen_png = self.session.screen_png
        self.session._ensure_keyboard = mock.AsyncMock()
        self.session._stroke = mock.AsyncMock()
        self.session._release_all = mock.AsyncMock()
        self.session.lost = mock.Mock()
        self.session._input_task = asyncio.create_task(self.session._input_worker())
        self.addAsyncCleanup(self.session._stop_input)
        self.session.hub.set_config(StreamConfig("test", 100, 200, b"test"))
        self.frame = 0

    def patch(self, name, replacement):
        patcher = mock.patch.object(passcode, name, replacement)
        patcher.start()
        self.addCleanup(patcher.stop)
        return replacement

    def current_grant(self):
        raw = self.store.get(passcode.AUTO_TARGET)
        return json.loads(raw) if raw is not None else None

    def publish(self):
        self.frame += 1
        self.entry.notify_startup_event("bundle-capabilities")

    async def start(self):
        entry = self.entry = passcode.XCTestAutoEntry(self.session)
        task = asyncio.create_task(entry.watch())
        self.addAsyncCleanup(self.cancel_task, task)
        await asyncio.sleep(0)  # Let watch subscribe before publishing an event.
        return entry, task

    async def cancel_task(self, task):
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, passcode.PasscodeError):
            pass

    async def complete_entry(self):
        entry, task = await self.start()
        self.publish()
        await task
        return entry

    async def test_video_off_enters_once_without_video_frames_and_rearms_on_ready(self):
        self.session.info.video_paused = "off"
        entry, task = await self.start()
        self.recognize.assert_not_awaited()
        entry.notify_startup_event("bundle-capabilities")
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.recognize.await_count, 2)
        self.assertEqual(self.session._stroke.await_count, len(passcode.strokes(SECRET)))
        self.session.screen_png.assert_not_awaited()
        self.assertTrue(self.current_grant()["blocked"])
        self.assertEqual(self.session.hub.subscribers, set())
        entry.confirmed()
        self.assertEqual(self.session.info.passcode_state, "confirmed")
        self.assertFalse(self.current_grant()["blocked"])

    async def test_delayed_ax_prompt_after_single_startup_event(self):
        self.recognize.side_effect = [LOCK_SCREEN, PROMPT, PROMPT]
        entry, task = await self.start()
        self.recognize.assert_not_awaited()
        entry.notify_startup_event("bundle-capabilities")
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.recognize.await_count, 3)
        self.session.screen_png.assert_not_awaited()
        self.assertEqual(self.session._stroke.await_count, len(passcode.strokes(SECRET)))

    async def test_unknown_and_late_events_cannot_start_ax_reads(self):
        entry, task = await self.start()
        entry.notify_startup_event("unknown")
        await asyncio.sleep(0)
        self.recognize.assert_not_awaited()
        await self.cancel_task(task)
        entry.notify_startup_event("bundle-capabilities")
        self.recognize.assert_not_awaited()

    async def test_startup_event_before_subscription_is_not_lost(self):
        entry = passcode.XCTestAutoEntry(self.session)
        entry.notify_startup_event("bundle-capabilities")
        await asyncio.wait_for(entry.watch(), 1)
        self.assertEqual(self.session._stroke.await_count, len(passcode.strokes(SECRET)))


    async def test_grant_is_persistently_blocked_before_first_key(self):
        observed = []

        async def stroke(usage, modifiers):
            observed.append(self.current_grant()["blocked"])
            self.assertTrue(passcode._entering)

        self.session._stroke.side_effect = stroke
        await self.complete_entry()
        self.assertTrue(observed)
        self.assertTrue(all(observed))
        self.assertEqual(len(self.write_events), 1)
        self.assertEqual(self.session.info.passcode_state, "blocked")
        self.assertFalse(passcode._entering)
        # A new WDA attempt/process sees the durable block, not a new chance.
        _, second = await self.start()
        await second
        self.assertEqual(self.session._stroke.await_count, len(observed))

    async def test_only_confirmation_rearms_a_completed_entry(self):
        entry = await self.complete_entry()
        self.assertTrue(self.current_grant()["blocked"])
        entry.confirmed()
        self.assertEqual(self.current_grant(), self.grant)
        self.assertEqual(self.session.info.passcode_state, "confirmed")
        writes = len(self.write_events)
        entry.confirmed()
        self.assertEqual(len(self.write_events), writes)

    async def test_confirmation_without_entry_does_not_create_a_grant(self):
        self.store.pop(passcode.AUTO_TARGET)
        entry, task = await self.start()
        await task
        entry.confirmed()
        self.assertIsNone(self.current_grant())
        self.write.assert_not_called()

    async def test_disable_after_entry_is_not_undone_by_confirmation(self):
        entry = await self.complete_entry()
        await passcode.configure_automatic(self.session, False)
        entry.confirmed()
        self.assertIsNone(self.current_grant())

    async def test_save_after_entry_invalidates_grant_and_confirmation(self):
        entry = await self.complete_entry()
        await passcode.save("123456")
        entry.confirmed()
        self.assertEqual(self.store[passcode.TARGET], "123456")
        self.assertIsNone(self.current_grant())

    async def test_new_grant_is_not_overwritten_by_old_confirmation(self):
        entry = await self.complete_entry()
        await passcode.configure_automatic(self.session, True)
        replacement = self.current_grant()
        self.assertNotEqual(replacement["token"], self.grant["token"])
        entry.confirmed()
        self.assertEqual(self.current_grant(), replacement)

    async def test_disabled_other_device_blocked_and_paused_do_not_read_screen(self):
        for grant, paused in ((None, None), ({**self.grant, "udid": "other-phone"}, None),
                              ({**self.grant, "blocked": True}, None), (self.grant, "protected")):
            with self.subTest(grant=grant, paused=paused):
                if grant is None:
                    self.store.pop(passcode.AUTO_TARGET, None)
                else:
                    self.store[passcode.AUTO_TARGET] = json.dumps(grant)
                self.session.info.video_paused = paused
                _, task = await self.start()
                await task
                self.assertEqual(self.session.hub.subscribers, set())
        self.session.screen_png.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))










    async def test_legacy_general_scope_cannot_enter_device_or_app_prompts(self):
        self.store[passcode.AUTO_TARGET] = json.dumps({**self.grant, "other_prompts": True})
        _, task = await self.start()
        for index, lines in enumerate(NON_XCTEST_PROMPTS):
            self.recognize.return_value = lines
            self.session.screen_png.return_value = (str(index).encode(), 100, 200)
            self.publish()
            await asyncio.sleep(0)
        self.assertEqual(self.recognize.await_count, len(NON_XCTEST_PROMPTS))
        self.session.hub.close()
        await task
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))
        self.assertEqual(self.current_grant(), self.grant)

    async def test_cancel_unsubscribes_and_releases_entry_ownership(self):
        _, task = await self.start()
        self.assertEqual(len(self.session.hub.subscribers), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.session.hub.subscribers, set())
        self.assertFalse(passcode._entering)
        self.assertEqual(self.session.info.passcode_state, "stopped")
        self.session._stroke.assert_not_awaited()

    async def test_recognition_exception_is_redacted_and_unsubscribed(self):
        self.recognize.side_effect = RuntimeError(SECRET)
        _, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError) as raised:
            await task
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertEqual(self.session.hub.subscribers, set())
        self.assertEqual(self.current_grant(), self.grant)
        self.session._stroke.assert_not_awaited()

    async def test_no_input_if_prompt_changes_while_command_waits(self):
        self.recognize.side_effect = [PROMPT, LOCK_SCREEN]
        _, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError):
            await task
        self.session._stroke.assert_not_awaited()
        self.assertEqual(self.current_grant(), self.grant)

    async def occupy_input_queue(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def earlier_input(text):
            entered.set()
            await release.wait()
            return {"ok": True}

        self.session._type_text = mock.AsyncMock(side_effect=earlier_input)
        pending = asyncio.create_task(self.session.run({"op": "text", "text": "earlier input"}))
        await entered.wait()
        return release, pending

    async def test_disabling_queued_entry_prevents_keyboard_and_secret_access(self):
        release, pending = await self.occupy_input_queue()
        _, task = await self.start()
        self.publish()
        await asyncio.sleep(0)
        self.assertEqual(self.session._input.qsize(), 1)
        self.read.reset_mock()
        await passcode.configure_automatic(self.session, False)
        release.set()
        await pending
        with self.assertRaises(passcode.PasscodeError):
            await task
        self.session._ensure_keyboard.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_cancelled_queued_entry_is_discarded_without_replay(self):
        release, pending = await self.occupy_input_queue()
        _, task = await self.start()
        self.publish()
        await asyncio.sleep(0)
        self.assertEqual(self.session._input.qsize(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        release.set()
        await pending
        await asyncio.sleep(0)
        self.session._ensure_keyboard.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertEqual(self.session._input.qsize(), 0)
        self.assertEqual(self.session.hub.subscribers, set())
        self.assertEqual(self.current_grant(), self.grant)

    async def test_disabling_mid_input_stops_remaining_keys_and_return(self):
        async def stroke(usage, modifiers):
            self.store.pop(passcode.AUTO_TARGET)

        self.session._stroke.side_effect = stroke
        entry, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError):
            await task
        self.assertEqual(self.session._stroke.await_count, 1)
        entry.confirmed()
        self.assertIsNone(self.current_grant())

    async def test_secret_entry_does_not_register_keyboard(self):
        self.session._ensure_keyboard.side_effect = AssertionError("must use existing Indigo path")
        await self.complete_entry()
        self.session._ensure_keyboard.assert_not_awaited()
        self.assertEqual(self.session._stroke.await_count, len(passcode.strokes(SECRET)))

    async def test_disabling_during_prepare_prevents_secret_read(self):
        calls = 0

        async def recognize(png):
            nonlocal calls
            calls += 1
            if calls == 2:
                await passcode.configure_automatic(self.session, False)
            return PROMPT

        self.recognize.side_effect = recognize
        _, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError):
            await task
        self.session._ensure_keyboard.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_missing_or_invalid_saved_secret_never_reaches_hid(self):
        for value in (None, "", "12", "not a passcode"):
            with self.subTest(value=value):
                self.store[passcode.TARGET] = value
                _, task = await self.start()
                self.publish()
                with self.assertRaises(passcode.PasscodeError):
                    await task
                self.assertEqual(self.current_grant(), self.grant)
        self.session._stroke.assert_not_awaited()
        self.write.assert_not_called()

    async def test_corrupt_grant_fails_closed_without_reading_screen_or_secret(self):
        for raw in ("not json", "null", "[]", json.dumps({**self.grant, "blocked": "false"})):
            with self.subTest(raw=raw):
                self.store[passcode.AUTO_TARGET] = raw
                _, task = await self.start()
                with self.assertRaises(passcode.PasscodeError):
                    await task
        self.session.screen_png.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_cancel_mid_input_keeps_persistent_block_and_never_rearms(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def stroke(usage, modifiers):
            entered.set()
            await release.wait()

        self.session._stroke.side_effect = stroke
        entry, task = await self.start()
        self.publish()
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())  # The in-flight key must finish first.
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        self.assertEqual(self.session._stroke.await_count, 1)
        self.assertTrue(self.current_grant()["blocked"])
        entry.confirmed()
        self.assertTrue(self.current_grant()["blocked"])
        self.assertFalse(passcode._entering)
        self.assertEqual(self.session.hub.subscribers, set())

    async def test_manual_entry_in_progress_prevents_automatic_input(self):
        with mock.patch.object(passcode, "_entering", True):
            _, task = await self.start()
            self.publish()
            await task
            self.assertTrue(passcode._entering)
        self.session._stroke.assert_not_awaited()
        self.assertEqual(self.current_grant(), self.grant)

    async def test_automatic_entry_ownership_refuses_manual_entry(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def stroke(usage, modifiers):
            entered.set()
            await release.wait()

        self.session._stroke.side_effect = stroke
        _, task = await self.start()
        self.publish()
        await entered.wait()
        with self.assertRaises(passcode.PasscodeError):
            await passcode.enter(self.session)
        release.set()
        await task

    async def test_storage_failure_prevents_any_keys_and_does_not_leak_exception(self):
        self.write.side_effect = OSError(SECRET)
        _, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError) as raised:
            await task
        self.session._stroke.assert_not_awaited()
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(self.current_grant(), self.grant)

    async def test_partial_field_never_reads_or_sends_secret(self):
        self.recognize.return_value = PROMPT + [{"text": "●○○○○○"}]
        _, task = await self.start()
        self.publish()
        await asyncio.sleep(0)
        self.session.hub.close()
        await task
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_user_input_during_detection_waits_for_fresh_stable_frame(self):
        calls = 0

        async def recognize(png):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.session.passcode_input_generation += 1
            return PROMPT

        self.recognize.side_effect = recognize
        _, task = await self.start()
        self.publish()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))
        self.assertEqual(len(self.session.hub.subscribers), 1)
        # A subsequent video event can show identical pixels; it still needs
        # a fresh OCR check after the preceding input/IME operation completed.
        self.publish()
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.recognize.await_count, 3)
        self.assertEqual(self.session._stroke.await_count, len(passcode.strokes(SECRET)))

    async def test_repeated_detection_input_changes_never_submit_a_secret(self):
        async def recognize(png):
            self.session.passcode_input_generation += 1
            return PROMPT

        self.recognize.side_effect = recognize
        _, task = await self.start()
        for _ in range(3):
            self.publish()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))
        self.session.hub.close()
        await asyncio.wait_for(task, 2)

    async def test_user_input_during_prepare_stops_entry_before_secret_read(self):
        calls = 0

        async def recognize(png):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.session.passcode_input_generation += 1
            return PROMPT

        self.recognize.side_effect = recognize
        _, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError):
            await asyncio.wait_for(task, 2)
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_user_input_during_secret_send_stops_remaining_keys(self):
        async def stroke(usage, modifiers):
            self.session.passcode_input_generation += 1

        self.session._stroke.side_effect = stroke
        entry, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError):
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.session._stroke.await_count, 1)
        entry.confirmed()
        self.assertTrue(self.current_grant()["blocked"])

    async def test_input_failure_is_redacted_never_retried_and_stays_blocked(self):
        self.session._stroke.side_effect = OSError(SECRET)
        entry, task = await self.start()
        self.publish()
        with self.assertRaises(passcode.PasscodeError) as raised:
            await task
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(self.session._stroke.await_count, 1)
        self.session.lost.assert_called_once()
        self.assertNotIn(SECRET, str(self.session.lost.call_args))
        entry.confirmed()
        self.assertTrue(self.current_grant()["blocked"])

    async def test_enabling_requires_saved_secret_and_connected_device(self):
        self.store.pop(passcode.AUTO_TARGET)
        self.session.info.state = "closed"
        with self.assertRaises(passcode.PasscodeError):
            await passcode.configure_automatic(self.session, True)
        self.session.info.state = "ready"
        self.store.pop(passcode.TARGET)
        with self.assertRaises(passcode.PasscodeError):
            await passcode.configure_automatic(self.session, True)
        self.write.assert_not_called()

    async def test_enabling_exposes_no_secret_or_device_identifiers(self):
        self.store.pop(passcode.AUTO_TARGET)
        result = await passcode.configure_automatic(self.session, True)
        self.assertEqual(result, {"ok": True, "automatic": {
            "enabled": True, "blocked": False, "device_matches": True}})
        self.assertEqual(self.current_grant()["udid"], DEVICE)
        self.assertNotIn(SECRET, json.dumps(result))

    async def test_legacy_scope_migration_preserves_identity_and_consumed_block(self):
        for old_scope in (True, False, "obsolete"):
            for blocked in (True, False):
                with self.subTest(old_scope=old_scope, blocked=blocked):
                    expected = {**self.grant, "blocked": blocked}
                    self.store[passcode.AUTO_TARGET] = json.dumps({**expected, "other_prompts": old_scope})
                    self.write.reset_mock()
                    self.read.reset_mock()
                    status = passcode.auto_status(DEVICE)
                    self.assertEqual(status, {"enabled": True, "blocked": blocked, "device_matches": True})
                    self.assertEqual(self.current_grant(), expected)
                    self.assertEqual(passcode._read_auto(), expected)
                    self.write.assert_called_once()
                    self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_legacy_scope_migration_failure_stops_without_secret_or_capture(self):
        self.store[passcode.AUTO_TARGET] = json.dumps({**self.grant, "other_prompts": True})
        self.write.side_effect = RuntimeError(SECRET)
        _, task = await self.start()
        with self.assertRaises(passcode.PasscodeError) as raised:
            await task
        self.assertNotIn(SECRET, str(raised.exception))
        self.session.screen_png.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertFalse(any(call.args == (passcode.TARGET,) for call in self.read.call_args_list))

    async def test_removed_general_scope_cannot_be_reenabled(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value), self.assertRaises(passcode.PasscodeError):
                await passcode.configure_automatic(self.session, value)
        with self.assertRaises(TypeError):
            await passcode.configure_automatic(self.session, True, other_prompts=True)
        self.write.assert_not_called()
        self.read.assert_not_called()

    async def test_secret_strokes_refuses_held_keys_and_clears_command(self):
        self.session._keys_down.add(0xE1)
        prepare = mock.AsyncMock(return_value=passcode.strokes(SECRET))
        command = {"op": "secret_strokes", "prepare": prepare, "guard": lambda: True}
        with self.assertRaises(passcode.PasscodeError):
            await self.session.run(command)
        prepare.assert_not_awaited()
        self.session._stroke.assert_not_awaited()
        self.assertEqual(command, {})

    async def test_secret_strokes_scrubs_payload_on_transport_and_other_errors(self):
        for error in (OSError(SECRET), RuntimeError(SECRET)):
            with self.subTest(error=type(error).__name__):
                self.session._stroke.side_effect = error
                prepared = passcode.strokes(SECRET)
                command = {"op": "secret_strokes", "strokes": prepared}
                with self.assertRaises((SessionError, passcode.PasscodeError)) as raised:
                    await self.session.run(command)
                self.assertNotIn(SECRET, str(raised.exception))
                self.assertEqual(prepared, [])
                self.assertEqual(command, {})

    async def test_wda_startup_real_watcher_and_input_queue_rearm_only_after_ready(self):
        await self.assert_wda_startup_with_real_watcher(call_mode=False)

    async def test_call_wda_listener_real_watcher_and_queue_rearm_only_after_ready(self):
        await self.assert_wda_startup_with_real_watcher(call_mode=True)

    async def assert_wda_startup_with_real_watcher(self, call_mode):
        from ictrl.wda import WdaAgent

        if call_mode:
            self.session.info.video_paused = "call"
            self.session.passcode_screen_png = mock.AsyncMock(return_value=(b"fresh-call", 100, 200))

        agent = WdaAgent(self.session)
        agent.runner = "test.xctrunner"
        agent._kill_old_runner = mock.AsyncMock()
        self.addAsyncCleanup(agent.stop)
        ready = asyncio.Event()
        runner_stopped = asyncio.Event()
        seen = []

        async def stroke(usage, modifiers):
            self.assertTrue(self.current_grant()["blocked"])
            seen.append(usage)
            if usage == 0x28:  # The last Return key allows the fake WDA to start.
                ready.set()

        async def run(config, *, listener):
            self.assertEqual(len(self.session.hub.subscribers), 1)
            await listener.test_bundle_ready_with_capabilities(object())
            try:
                await asyncio.Future()
            finally:
                runner_stopped.set()

        async def request(method, path, **kwargs):
            self.assertEqual((method, path), ("GET", "/status"))
            await ready.wait()
            return {"value": {"ready": True}}

        async def configure():
            self.assertTrue(ready.is_set())
            self.assertTrue(self.current_grant()["blocked"])

        self.session._stroke.side_effect = stroke
        agent.request = mock.AsyncMock(side_effect=request)
        agent._configure = mock.AsyncMock(side_effect=configure)
        xcuitest = "pymobiledevice3.services.dvt.testmanaged.xcuitest"
        runner = mock.AsyncMock(side_effect=run)
        with mock.patch(xcuitest + ".TestConfig.create_for", mock.AsyncMock(return_value=SimpleNamespace())), \
                mock.patch(xcuitest + ".XCUITestService", return_value=SimpleNamespace(run=runner)):
            await asyncio.wait_for(agent.ensure(), 2)
        self.assertEqual(seen, [usage for usage, _ in passcode.strokes(SECRET)])
        agent._configure.assert_awaited_once()
        runner.assert_awaited_once()
        self.assertTrue(agent._ready)
        self.assertTrue(self.session.info.automation)
        self.assertEqual(self.session.info.passcode_state, "confirmed")
        self.assertEqual(self.current_grant(), self.grant)
        self.assertEqual(self.session.hub.subscribers, set())
        if call_mode:
            self.session.screen_png.assert_not_awaited()
            self.session.passcode_screen_png.assert_not_awaited()
            self.assertEqual(self.frame, 0)
            listener = runner.await_args.kwargs["listener"]
            await listener.did_begin_initializing_for_ui_testing()
            await asyncio.sleep(0)
            self.session.passcode_screen_png.assert_not_awaited()
        self.assertFalse(runner_stopped.is_set())
        await agent.stop()
        self.assertTrue(runner_stopped.is_set())


if __name__ == "__main__":
    unittest.main()
