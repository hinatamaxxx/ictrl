"""The removed general-unlock action cannot dispatch device input."""
import json
import types
import unittest
from unittest import mock

from starlette.requests import Request

from ictrl import server
from ictrl.session import SessionError


class UnlockRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = types.SimpleNamespace(ready=True)
        self.manager = mock.Mock(session=self.session)
        self.manager.require.return_value = self.session
        with mock.patch.object(server, "Manager", return_value=self.manager):
            app = server.create_app(47800)
        self.route = next(route for route in app.routes
                          if getattr(route, "path", None) == "/api/passcode/{action}")

    async def request(self, headers):
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        request = Request({
            "type": "http", "method": "POST", "path": "/api/passcode/unlock", "query_string": b"",
            "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
            "path_params": {"action": "unlock"}}, receive)
        response = await self.route.endpoint(request)
        return response.status_code, json.loads(response.body)

    async def test_removed_unlock_is_not_found_even_from_local_gui(self):
        for origin in ("http://127.0.0.1:47800", "http://localhost:47800"):
            with self.subTest(origin=origin):
                status, result = await self.request({"user-agent": "Mozilla/5.0", "origin": origin})
                self.assertEqual(status, 404)
                self.assertFalse(result["ok"])
        self.manager.require.assert_not_called()

    async def test_missing_gui_identity_or_wrong_origin_never_dispatches(self):
        denied = ({}, {"user-agent": "ictrl-mcp"},
                  {"user-agent": "ictrl-mcp", "origin": "http://127.0.0.1:47800"},
                  {"user-agent": "Mozilla/5.0"},
                  {"user-agent": "Mozilla/5.0", "origin": "https://other.example"},
                  {"user-agent": "Mozilla/5.0", "origin": "http://127.0.0.1:47801"})
        for headers in denied:
            with self.subTest(headers=headers):
                status, _result = await self.request(headers)
                self.assertEqual(status, 403)
        self.manager.require.assert_not_called()

    async def test_disconnected_gui_request_does_not_start_unlock(self):
        self.manager.require.side_effect = SessionError("iPhoneに接続されていません")
        status, result = await self.request({"user-agent": "Mozilla/5.0",
                                             "origin": "http://127.0.0.1:47800"})
        self.assertEqual(status, 404)
        self.assertFalse(result["ok"])
        self.manager.require.assert_not_called()


if __name__ == "__main__":
    unittest.main()
