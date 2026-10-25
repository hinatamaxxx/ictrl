"""Screen-element logic of utsushite.wda, from trees and cases seen on iOS 27."""
import asyncio
import contextlib
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest import mock

from utsushite import passcode, wda


def tree(xml: str):
    return ET.fromstring(xml)


# Clock's "add alarm" sheet (reduced): the alarm list is in the
# window's first branch, the sheet (not full screen) in a later branch.
SHEET = """
<XCUIElementTypeApplication x="0" y="0" width="402" height="874">
  <XCUIElementTypeWindow x="0" y="0" width="402" height="874">
    <XCUIElementTypeOther x="0" y="0" width="402" height="874">
      <XCUIElementTypeButton label="編集" x="262" y="66" width="64" height="36"/>
      <XCUIElementTypeCell label="20:09, アラーム" x="0" y="200" width="402" height="108"/>
      <XCUIElementTypeCell label="20:05, アラーム" x="0" y="0" width="402" height="60"/>
    </XCUIElementTypeOther>
    <XCUIElementTypeOther x="0" y="0" width="402" height="874">
      <XCUIElementTypeOther x="0" y="62" width="402" height="812">
        <XCUIElementTypeButton label="キャンセル" x="20" y="82" width="36" height="36"/>
        <XCUIElementTypeOther x="0" y="62" width="402" height="812">
          <XCUIElementTypeSwitch label="スヌーズ" x="300" y="600" width="60" height="30"/>
        </XCUIElementTypeOther>
      </XCUIElementTypeOther>
    </XCUIElementTypeOther>
  </XCUIElementTypeWindow>
</XCUIElementTypeApplication>
"""


class CoveredTests(unittest.TestCase):
    def labels(self, xml):
        root = tree(xml)
        hidden = wda._covered(root, 402, 874)
        return {n.get("label") for n in root.iter() if n.get("label") and id(n) not in hidden}

    def test_sheet_hides_the_list_behind_it(self):
        visible = self.labels(SHEET)
        self.assertIn("キャンセル", visible)
        self.assertIn("スヌーズ", visible)          # a transparent layer inside the sheet hides nothing
        self.assertNotIn("編集", visible)
        self.assertNotIn("20:09, アラーム", visible)
        self.assertIn("20:05, アラーム", visible)    # its centre is above the sheet

    def test_plain_screen_hides_nothing(self):
        plain = SHEET.replace('<XCUIElementTypeOther x="0" y="62" width="402" height="812">',
                              '<XCUIElementTypeOther x="0" y="0" width="402" height="874">')
        self.assertIn("編集", self.labels(plain))   # full-screen hosts are structure, not covers


class GeometryTests(unittest.TestCase):
    def test_portrait_is_unchanged(self):
        self.assertEqual(wda._to_portrait(0.2, 0.7, None), (0.2, 0.7))

    def test_landscape_maps_to_panel(self):
        # Calculator's "1" key in LANDSCAPE was at app (0.1682, 0.7313) and on
        # the portrait panel at (0.2687, 0.1682).
        x, y = wda._to_portrait(0.1682, 0.7313, "LANDSCAPE")
        self.assertAlmostEqual(x, 0.2687, places=3)
        self.assertAlmostEqual(y, 0.1682, places=3)

    def test_widget_content_is_shifted_to_screen(self):
        root = tree("""
        <XCUIElementTypeApplication x="0" y="0" width="402" height="874">
          <XCUIElementTypeIcon label="LINE Calendar" x="24" y="88" width="169" height="197">
            <XCUIElementTypeOther x="26" y="90" width="165" height="179">
              <XCUIElementTypeStaticText label="2026" x="60" y="20" width="30" height="20"/>
            </XCUIElementTypeOther>
          </XCUIElementTypeIcon>
        </XCUIElementTypeApplication>""")
        positions = {n.get("label"): (x, y) for n, x, y in wda._absolute(root) if n.get("label")}
        self.assertEqual(positions["2026"], (86.0, 110.0))


class MatchingTests(unittest.TestCase):
    found = [{"label": "10月15日(木)"}, {"label": "設定"}, {"label": "Settings Pro"},
             {"label": "_TtGC7SwiftUI32NavigationStackHosting"}]

    def find(self, text):
        return asyncio.run(wda.WdaAgent(None).find(text, 0, self.found))["label"]

    def test_short_text_is_exact_only(self):
        with self.assertRaises(wda.WdaError):
            self.find("1")                              # not "10月15日(木)"

    def test_prefix_for_longer_text(self):
        self.assertEqual(self.find("settings"), "Settings Pro")

    def test_internal_identifiers(self):
        self.assertTrue(wda._internal("_TtGC7SwiftUI32NavigationStackHosting"))
        self.assertTrue(wda._internal("com.apple.settings.sidebar.collectionView"))
        self.assertFalse(wda._internal("Wi-Fi"))
        self.assertFalse(wda._internal("example.com"))


class LaunchMessageTests(unittest.TestCase):
    def test_signature_problem_is_named(self):
        message = wda.start_failure_message(RuntimeError("provisioning profile has expired"))
        self.assertIn("AltStore", message)
        self.assertIn("署名", message)


class StartupListenerTests(unittest.TestCase):
    def test_callbacks_enqueue_fixed_kinds_without_suspending_or_logging_payloads(self):
        from pymobiledevice3.services.dvt.testmanaged.dtx_services import XCUITestListener

        class PrivatePayload:
            def __repr__(self):
                raise AssertionError("callback payload must not be inspected or logged")

        entry = SimpleNamespace(notify_startup_event=mock.Mock())
        listener = wda._XCTestStartupListener(entry)
        self.assertIsInstance(listener, XCUITestListener)
        payload = PrivatePayload()
        callbacks = [(listener.test_bundle_ready, (), "bundle-ready"),
                     (listener.test_bundle_ready_with_protocol_version, (payload, payload), "bundle-protocol"),
                     (listener.test_bundle_ready_with_capabilities, (payload,), "bundle-capabilities"),
                     (listener.did_begin_initializing_for_ui_testing, (), "ui-initializing"),
                     (listener.did_begin_executing_test_plan, (), "test-plan-started")]
        with self.assertLogs("utsushite.wda", level="INFO") as captured:
            for callback, args, kind in callbacks:
                with self.subTest(kind=kind):
                    # A callback must finish in its first coroutine step. Any
                    # awaited capture/queue task would suspend instead.
                    with self.assertRaises(StopIteration) as completed:
                        callback(*args).send(None)
                    self.assertIsNone(completed.exception.value)
                    entry.notify_startup_event.assert_called_with(kind)
        self.assertEqual(entry.notify_startup_event.call_args_list,
                         [mock.call(kind) for _, _, kind in callbacks])
        self.assertEqual(captured.output,
                         [f"INFO:utsushite.wda:XCTest startup event: {kind}" for _, _, kind in callbacks])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.session = SimpleNamespace(rsd=object(), _set=mock.Mock())
        self.agent = wda.WdaAgent(self.session)
        self.agent._find_runner = mock.AsyncMock(return_value="test.runner")
        self.agent._kill_old_runner = mock.AsyncMock()
        self.agent._configure = mock.AsyncMock()
        self.runner_started, self.runner_ended = asyncio.Event(), asyncio.Event()
        self.runner_release = asyncio.Event()
        self.helper_started, self.helper_ended = asyncio.Event(), asyncio.Event()
        self.helper_release, self.ready = asyncio.Event(), asyncio.Event()
        self.helper_error = None
        self.entry = SimpleNamespace(sending=False, confirmed=mock.Mock(), notify_startup_event=mock.Mock())

        async def run(_cfg, *, listener):
            self.listener = listener
            self.runner_started.set()
            try:
                await self.runner_release.wait()
            finally:
                self.runner_ended.set()

        async def watch():
            self.helper_started.set()
            try:
                await self.helper_release.wait()
                if self.helper_error:
                    raise self.helper_error
            finally:
                self.helper_ended.set()

        async def request(*_args, **_kwargs):
            await self.ready.wait()
            return {"value": {"ready": True}}

        self.entry.watch = mock.AsyncMock(side_effect=watch)
        self.agent.request = mock.AsyncMock(side_effect=request)
        self.run = mock.AsyncMock(side_effect=run)
        module = "pymobiledevice3.services.dvt.testmanaged.xcuitest"
        self.stack.enter_context(mock.patch(module + ".TestConfig.create_for",
                                           mock.AsyncMock(return_value=SimpleNamespace())))
        self.stack.enter_context(mock.patch(module + ".XCUITestService",
                                           return_value=SimpleNamespace(run=self.run)))
        self.stack.enter_context(mock.patch.object(passcode, "XCTestAutoEntry",
                                                  return_value=self.entry, create=True))

    async def asyncTearDown(self):
        await self.agent.stop()
        self.assertFalse([t for t in asyncio.all_tasks()
                          if not t.done() and t.get_name().startswith("utsushite-wda")])

    async def start(self):
        caller = asyncio.create_task(self.agent.ensure())
        await asyncio.wait_for(self.helper_started.wait(), 2)
        await asyncio.wait_for(self.runner_started.wait(), 2)
        return caller

    async def test_ready_stops_unsent_helper_before_configuring(self):
        caller = await self.start()
        self.agent._configure.side_effect = lambda: self.assertTrue(self.helper_ended.is_set())
        self.ready.set()
        await caller
        self.assertTrue(self.agent._ready)
        self.entry.confirmed.assert_called_once_with()
        self.assertFalse(self.runner_ended.is_set())

    async def test_runner_gets_listener_and_cleanup_still_joins_helper(self):
        caller = await self.start()
        self.assertIs(self.run.call_args.kwargs["listener"], self.listener)
        self.assertIsInstance(self.listener, wda._XCTestStartupListener)
        await self.listener.did_begin_initializing_for_ui_testing()
        self.entry.notify_startup_event.assert_called_once_with("ui-initializing")
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertTrue(self.helper_ended.is_set())
        self.assertTrue(self.runner_ended.is_set())
        self.entry.confirmed.assert_not_called()

    async def test_ready_finishes_one_entry_already_being_sent(self):
        caller = await self.start()
        waiting_for_entry = asyncio.Event()
        self.entry.sending = mock.MagicMock()
        self.entry.sending.__bool__.side_effect = lambda: waiting_for_entry.set() or True
        # Release the request already in flight; the helper remains sending.
        self.ready.set()
        await asyncio.wait_for(waiting_for_entry.wait(), 2)
        self.assertFalse(caller.done())
        self.agent._configure.assert_not_awaited()
        self.helper_release.set()
        await caller
        self.entry.confirmed.assert_called_once_with()

    async def test_concurrent_ensure_waits_for_configuration(self):
        first = asyncio.create_task(self.agent.ensure())
        second = asyncio.create_task(self.agent.ensure())
        await self.helper_started.wait()
        configured, finish_config = asyncio.Event(), asyncio.Event()

        async def configure():
            configured.set()
            await finish_config.wait()

        self.agent._configure.side_effect = configure
        self.ready.set()
        await configured.wait()
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.run.assert_awaited_once()
        finish_config.set()
        await asyncio.gather(first, second)

    async def test_one_cancelled_waiter_keeps_other_request_alive(self):
        first = asyncio.create_task(self.agent.ensure())
        second = asyncio.create_task(self.agent.ensure())
        await self.helper_started.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.helper_ended.is_set())
        self.ready.set()
        await second
        self.run.assert_awaited_once()

    async def test_last_cancelled_waiter_cleans_up_both_tasks(self):
        caller = await self.start()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertTrue(self.helper_ended.is_set())
        self.assertTrue(self.runner_ended.is_set())
        self.assertIsNone(self.agent._task)
        self.entry.confirmed.assert_not_called()

    async def test_stop_cancels_start_and_helper(self):
        caller = await self.start()
        await self.agent.stop()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertTrue(self.helper_ended.is_set())
        self.assertTrue(self.runner_ended.is_set())
        self.assertFalse(self.agent._ready)
        self.entry.confirmed.assert_not_called()

    async def test_stop_while_entry_is_sending_never_configures(self):
        caller = await self.start()
        waiting_for_entry = asyncio.Event()
        self.entry.sending = mock.MagicMock()
        self.entry.sending.__bool__.side_effect = lambda: waiting_for_entry.set() or True
        self.ready.set()
        # Reach the wait for the in-flight entry after readiness.
        await asyncio.wait_for(waiting_for_entry.wait(), 2)
        await self.agent.stop()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.agent._configure.assert_not_awaited()
        self.assertTrue(self.helper_ended.is_set())
        self.entry.confirmed.assert_not_called()

    async def test_new_start_waits_for_stop_to_finish(self):
        caller = await self.start()
        self.ready.set()
        await caller
        stopping, finish_stop = asyncio.Event(), asyncio.Event()

        async def kill():
            stopping.set()
            await finish_stop.wait()

        self.agent._kill_old_runner.side_effect = kill
        stop = asyncio.create_task(self.agent.stop())
        await stopping.wait()
        next_start = asyncio.create_task(self.agent.ensure())
        await asyncio.sleep(0)
        self.assertFalse(next_start.done())
        self.run.assert_awaited_once()
        finish_stop.set()
        await stop
        await next_start
        self.assertEqual(self.run.await_count, 2)
        self.assertTrue(self.agent._ready)

    async def test_timeout_has_no_surviving_launch_tasks(self):
        with mock.patch.object(wda, "START_TIMEOUT", 0):
            with self.assertRaises(wda.WdaError):
                await self.agent.ensure()
        self.assertIsNone(self.agent._task)
        self.assertFalse(self.agent._ready)
        self.entry.confirmed.assert_not_called()

    async def test_helper_error_stops_runner_without_retry(self):
        caller = await self.start()
        self.helper_error = passcode.PasscodeError("自動入力を中止しました")
        self.helper_release.set()
        await self.helper_ended.wait()
        self.ready.set()
        with self.assertRaisesRegex(wda.WdaError, "自動入力を中止"):
            await caller
        self.run.assert_awaited_once()
        self.assertTrue(self.runner_ended.is_set())
        self.entry.confirmed.assert_not_called()

    async def test_helper_error_interrupts_pending_readiness_request(self):
        caller = await self.start()
        self.helper_error = passcode.PasscodeError("自動入力を中止しました")
        self.helper_release.set()
        # /status stays pending; the helper failure is its own end event.
        with self.assertRaisesRegex(wda.WdaError, "自動入力を中止"):
            await asyncio.wait_for(caller, 2)
        self.assertTrue(self.runner_ended.is_set())
        self.run.assert_awaited_once()

    async def test_runner_exit_interrupts_pending_readiness_and_entry(self):
        caller = await self.start()
        self.entry.sending = True
        self.runner_release.set()
        with self.assertRaises(wda.WdaError):
            await asyncio.wait_for(caller, 2)
        self.assertTrue(self.helper_ended.is_set())
        self.agent._configure.assert_not_awaited()
        self.entry.confirmed.assert_not_called()

    async def test_runner_exit_during_configure_is_not_ready(self):
        caller = await self.start()

        async def configure():
            self.runner_release.set()
            await self.runner_ended.wait()

        self.agent._configure.side_effect = configure
        self.ready.set()
        with self.assertRaises(wda.WdaError):
            await caller
        self.assertFalse(self.agent._ready)
        self.entry.confirmed.assert_not_called()

    async def test_configure_failure_cleans_up_without_retry(self):
        caller = await self.start()
        self.agent._configure.side_effect = wda.WdaError("configuration failed")
        self.ready.set()
        with self.assertRaisesRegex(wda.WdaError, "configuration failed"):
            await caller
        self.agent._configure.assert_awaited_once()
        self.assertTrue(self.helper_ended.is_set())
        self.assertTrue(self.runner_ended.is_set())

    async def test_runner_exit_after_ready_clears_automation(self):
        caller = await self.start()
        self.ready.set()
        await caller
        automation_stopped = asyncio.Event()

        def changed(**state):
            if state.get("automation") is False:
                automation_stopped.set()

        self.session._set.side_effect = changed
        self.runner_release.set()
        await asyncio.wait_for(automation_stopped.wait(), 2)
        self.assertFalse(self.agent._ready)


if __name__ == "__main__":
    unittest.main()
