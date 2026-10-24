"""Real AX adapter, fake device responses; no credentials or screenshots."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from ictrl import xctest_prompt


HEAD = '"XCTest" を使うために\niPhoneのパスコードを入力'
EMPTY = 'パスコードフィールド, 6個中0個の値が入力済み'


class AccessibilityTests(unittest.IsolatedAsyncioTestCase):
    def setup_audit(self, labels, error=None):
        async def items():
            for label in labels:
                yield SimpleNamespace(caption=label, spoken_description=None)
            if error:
                raise error

        self.audit = mock.MagicMock()
        self.audit.__aenter__.return_value = self.audit
        self.audit.iter_elements = items
        self.audit.set_app_monitoring_enabled = mock.AsyncMock()
        self.audit.set_show_visuals = mock.AsyncMock()
        self.audit.close = mock.AsyncMock()
        patcher = mock.patch.object(xctest_prompt, 'AccessibilityAudit', return_value=self.audit)
        patcher.start()
        self.addCleanup(patcher.stop)
        session = SimpleNamespace(rsd=object(), screen_png=mock.AsyncMock(), passcode_screen_png=mock.AsyncMock())
        return session

    async def test_complete_prompt_without_images(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation', EMPTY, 'キャンセル'])
        self.assertTrue(await xctest_prompt.read_prompt(s))
        s.screen_png.assert_not_awaited()
        s.passcode_screen_png.assert_not_awaited()
        self.audit.set_app_monitoring_enabled.assert_awaited_once_with(False)
        self.audit.close.assert_awaited_once()
        self.audit.set_show_visuals.assert_awaited_once_with(False)

    async def test_live_ios27_ax_caption_with_role_and_word_joiners(self):
        s = self.setup_audit(['“XCTest”を使うためにiPhoneのパ\u2060ス\u2060コ\u2060ー\u2060ドを入力, 静的テキスト',
                             'Enable UI Automation, 静的テキスト', EMPTY, 'キャンセル, ボタン'])
        self.assertTrue(await xctest_prompt.read_prompt(s))

    async def test_stops_before_background_elements(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation', EMPTY], AssertionError('background read'))
        self.assertTrue(await xctest_prompt.read_prompt(s))

    async def test_requires_empty_field_even_with_valid_heading(self):
        for field in ('', 'パスコードフィールド', 'パスコードフィールド, 6個中1個の値が入力済み'):
            s = self.setup_audit([HEAD, 'Enable UI Automation', field])
            self.assertFalse(await xctest_prompt.read_prompt(s))

    async def test_field_before_heading_also_stops_at_required_labels(self):
        s = self.setup_audit([EMPTY, HEAD, 'Enable UI Automation'], AssertionError('background read'))
        self.assertTrue(await xctest_prompt.read_prompt(s))

    async def test_cannot_hide_overlay_stops_without_scanning(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation', EMPTY])
        self.audit.set_show_visuals.side_effect = OSError()
        with self.assertRaises(OSError):
            await xctest_prompt.read_prompt(s)

    def test_does_not_strip_arbitrary_comma_text_from_help(self):
        self.assertEqual(xctest_prompt.label_text(HEAD + ', help'), HEAD + ', help')

    async def test_full_cycle_checks_entered_digits_after_heading(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation', '●○○○○○'])
        self.assertFalse(await xctest_prompt.read_prompt(s))

    async def test_partial_scan_is_not_permission(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation'], TimeoutError())
        with self.assertRaises(TimeoutError):
            await xctest_prompt.read_prompt(s)
        self.audit.set_app_monitoring_enabled.assert_awaited_once_with(False)

    async def test_cancellation_closes_service(self):
        s = self.setup_audit([], asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await xctest_prompt.read_prompt(s)
        self.audit.close.assert_awaited_once()

    async def test_other_passcode_prompt_is_rejected(self):
        s = self.setup_audit(['iPhoneのパスコードを入力', 'キャンセル'])
        self.assertFalse(await xctest_prompt.read_prompt(s))

    async def test_does_not_accumulate_labels_across_reads(self):
        s = self.setup_audit([HEAD])
        self.assertFalse(await xctest_prompt.read_prompt(s))
        s = self.setup_audit(['Enable UI Automation', 'キャンセル'])
        self.assertFalse(await xctest_prompt.read_prompt(s))

    async def test_unbounded_tree_is_refused(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation'] + ['item'] * 161 + [EMPTY])
        self.assertFalse(await xctest_prompt.read_prompt(s))

    async def test_concurrent_requests_wait_until_provider_is_closed(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation', EMPTY])
        closing, release_close = asyncio.Event(), asyncio.Event()
        async def close(*args):
            closing.set()
            await release_close.wait()
        self.audit.close.side_effect = close
        first = asyncio.create_task(xctest_prompt.read_prompt(s))
        await closing.wait()
        second = asyncio.create_task(xctest_prompt.read_prompt(s))
        try:
            await asyncio.sleep(0)
            self.assertEqual(self.audit.__aenter__.await_count, 1)
            self.assertFalse(second.done())
            release_close.set()
            self.assertEqual(await asyncio.gather(first, second), [True, True])
            self.assertEqual(self.audit.__aenter__.await_count, 2)
        finally:
            release_close.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_cancel_waiting_reader_does_not_open_another_provider(self):
        s = self.setup_audit([HEAD, 'Enable UI Automation', EMPTY])
        s._ax_prompt_lock = asyncio.Lock()
        async with s._ax_prompt_lock:
            task = asyncio.create_task(xctest_prompt.read_prompt(s))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.audit.__aenter__.assert_not_awaited()
        self.assertTrue(await xctest_prompt.read_prompt(s))

    async def test_failed_scan_releases_lock_for_next_explicit_request(self):
        s = self.setup_audit([], OSError('device failure'))
        with self.assertRaises(OSError):
            await xctest_prompt.read_prompt(s)
        self.assertFalse(s._ax_prompt_lock.locked())
        self.audit.close.assert_awaited_once()

    async def test_cancel_during_connect_closes_before_next_reader(self):
        s = self.setup_audit([])
        connecting, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def connect():
            connecting.set()
            await asyncio.Event().wait()

        async def close():
            closing.set()
            await release.wait()

        self.audit.__aenter__.side_effect = connect
        self.audit.close.side_effect = close
        first = asyncio.create_task(xctest_prompt.read_prompt(s))
        await connecting.wait()
        first.cancel()
        await closing.wait()
        self.audit.__aenter__.side_effect = None
        second = asyncio.create_task(xctest_prompt.read_prompt(s))
        try:
            await asyncio.sleep(0)
            self.assertEqual(self.audit.__aenter__.await_count, 1)
            self.assertTrue(s._ax_prompt_lock.locked())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertFalse(await second)
            self.assertEqual(self.audit.close.await_count, 2)
        finally:
            release.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_failed_connect_is_closed_without_reading(self):
        s = self.setup_audit([HEAD, EMPTY])
        self.audit.__aenter__.side_effect = OSError('connect failed')
        with self.assertRaises(OSError):
            await xctest_prompt.read_prompt(s)
        self.audit.close.assert_awaited_once()
        self.audit.set_show_visuals.assert_not_awaited()
