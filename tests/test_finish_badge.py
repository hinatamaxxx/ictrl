"""An agent's finish (ictrl_finish, the Stop hook at the end of a response)
does not show "AI が操作中"; an ordinary agent request does."""
import types
import unittest
from unittest import mock

from starlette.requests import Request

from ictrl import server


def make_request(path):
    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}
    scope = {"type": "http", "method": "POST", "path": path, "query_string": b"",
             "headers": [(b"user-agent", b"python-urllib"), (b"content-type", b"application/json")]}
    return Request(scope, receive)


class FinishBadgeTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, path):
        manager = mock.Mock(session=None, agent_running=0, settings={})
        with mock.patch.object(server, "Manager", return_value=manager):
            app = server.create_app(47800)
        route = next(r for r in app.routes if getattr(r, "path", None) == path)
        await route.endpoint(make_request(path))
        notices = [c.args[0] for c in manager.notify.call_args_list if c.args[0].get("t") == "agent"]
        return manager, notices

    async def test_finish_shows_no_badge(self):
        manager, notices = await self.call("/api/automation/stop")
        self.assertEqual(notices, [])
        manager.agent_started.assert_not_called()
        manager.agent_finished.assert_called_once()

    async def test_an_action_shows_the_badge(self):
        manager, notices = await self.call("/api/disconnect")
        self.assertEqual([n["phase"] for n in notices], ["start", "end"])
        manager.agent_finished.assert_not_called()


if __name__ == "__main__":
    unittest.main()
