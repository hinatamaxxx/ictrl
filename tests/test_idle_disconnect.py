"""Nothing keeps reaching the phone when nobody uses it: no iCtrl window open
and no agent working closes the connection."""
import asyncio
import types
import unittest
from unittest import mock

from ictrl import manager as manager_module
from ictrl.manager import Manager


class IdleDisconnectTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        with mock.patch.object(Manager, "_load_settings", return_value={}), \
                mock.patch.object(Manager, "_load_known", return_value={}):
            m = Manager()
        m.session = types.SimpleNamespace(info=types.SimpleNamespace(state="ready"))
        m.disconnect = mock.AsyncMock()
        return m

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_agent_finished_without_a_window_disconnects_at_once(self):
        m = self.make()
        m.agent_started(); m.agent_ended(); m.agent_finished()
        await self.settle()
        m.disconnect.assert_awaited_once()

    async def test_an_open_window_keeps_the_connection(self):
        m = self.make()
        m.window_opened()
        m.agent_started(); m.agent_ended(); m.agent_finished()
        await self.settle()
        m.disconnect.assert_not_awaited()

    async def test_closing_the_last_window_waits_then_disconnects(self):
        m = self.make()
        with mock.patch.object(manager_module, "IDLE_AFTER_WINDOW", 0.01):
            m.window_opened(); m.window_closed()
            await asyncio.sleep(0.05)
        m.disconnect.assert_awaited_once()

    async def test_a_window_coming_back_cancels_it(self):
        m = self.make()
        with mock.patch.object(manager_module, "IDLE_AFTER_WINDOW", 0.05):
            m.window_opened(); m.window_closed()
            m.window_opened()                         # a reload
            await asyncio.sleep(0.1)
        m.disconnect.assert_not_awaited()

    async def test_an_agent_at_work_is_not_cut_off(self):
        m = self.make()
        m.agent_started()
        m.agent_finished()                            # another agent's finish while this one works
        await self.settle()
        m.disconnect.assert_not_awaited()

    async def test_the_server_ends_when_nothing_uses_it(self):
        m = self.make()
        m.session = None
        ended = []
        m.on_unused = lambda: ended.append(True)
        with mock.patch.object(manager_module, "SERVER_IDLE_EXIT", 0.01):
            m.page_opened(); m.page_closed()
            await asyncio.sleep(0.05)
        self.assertEqual(ended, [True])

    async def test_a_page_or_a_connection_keeps_the_server(self):
        m = self.make()
        ended = []
        m.on_unused = lambda: ended.append(True)
        with mock.patch.object(manager_module, "SERVER_IDLE_EXIT", 0.01):
            m.arm_exit()                              # still connected
            m.session = None
            m.page_opened()
            m.arm_exit()                              # a page (even hidden) is open
            await asyncio.sleep(0.05)
        self.assertEqual(ended, [])

    async def test_the_setting_turns_it_off(self):
        m = self.make()
        m.settings["auto_disconnect"] = False
        m.agent_started(); m.agent_ended(); m.agent_finished()
        await self.settle()
        m.disconnect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
