"""GUI-only automatic passcode configuration and credential-free status."""
import asyncio
import io
import json
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from starlette.requests import Request

from utsushite import mcp, passcode, server
from utsushite.session import SessionError


BROWSER = {"user-agent": "Mozilla/5.0", "origin": "http://127.0.0.1:47800"}


class AutomaticRouteTests(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(ready=True, info=SimpleNamespace(udid="test-iphone"))
        self.manager = mock.Mock(session=self.session)
        self.manager.require.return_value = self.session
        with mock.patch.object(server, "Manager", return_value=self.manager):
            self.app = server.create_app(47800)

    def request(self, method="POST", body=None, headers=None, action="automatic"):
        path = "/api/passcode" if method == "GET" else f"/api/passcode/{action}"
        template = "/api/passcode" if method == "GET" else "/api/passcode/{action}"
        route = next(r for r in self.app.routes if getattr(r, "path", None) == template)
        raw = json.dumps(body).encode()
        scope = {"type": "http", "method": method, "path": path, "query_string": b"",
                 "headers": [(k.encode(), v.encode()) for k, v in
                             (BROWSER if headers is None else headers).items()],
                 "path_params": {"action": action}}

        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}

        response = asyncio.run(route.endpoint(Request(scope, receive)))
        return response.status_code, json.loads(response.body)

    def test_enabled_requires_a_real_json_boolean(self):
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock) as configure:
            for body in ({}, {"enabled": "false"}, {"enabled": "true"}, {"enabled": 0},
                         {"enabled": 1}, {"enabled": None}, [], True):
                with self.subTest(body=body):
                    status, _ = self.request(body=body)
                    self.assertEqual(status, 400)
            configure.assert_not_called()
        self.manager.require.assert_not_called()

    def test_agents_and_foreign_origins_cannot_configure(self):
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock) as configure:
            for headers in ({"user-agent": "utsushite-mcp"},
                            {"user-agent": "utsushite-mcp", "origin": BROWSER["origin"]},
                            {"user-agent": "Mozilla/5.0"},
                            {**BROWSER, "origin": "https://other.example"}):
                for enabled in (True, False):
                    with self.subTest(headers=headers, enabled=enabled):
                        status, _ = self.request(body={"enabled": enabled}, headers=headers)
                        self.assertEqual(status, 403)
            configure.assert_not_called()

    def test_enable_uses_the_connected_session_and_notifies_the_gui(self):
        expected = {"enabled": True, "blocked": False, "device_matches": True}
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock,
                               return_value=expected) as configure:
            status, result = self.request(body={"enabled": True})
            self.assertEqual((status, result), (200, expected))
            configure.assert_awaited_once_with(self.session, True)
        self.manager.require.assert_called_once_with()
        self.manager.notify.assert_called_once_with({"t": "passcode"})

    def test_enable_without_connection_is_refused(self):
        self.manager.require.side_effect = SessionError("iPhone に接続してください")
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock) as configure:
            status, _ = self.request(body={"enabled": True})
            self.assertEqual(status, 409)
            configure.assert_not_called()

    def test_disable_works_without_a_connected_session(self):
        self.manager.session = None
        self.manager.require.side_effect = SessionError("not connected")
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock,
                               return_value={"enabled": False}) as configure:
            status, _ = self.request(body={"enabled": False})
            self.assertEqual(status, 200)
            configure.assert_awaited_once_with(None, False)
        self.manager.require.assert_not_called()

    def test_configuration_failure_is_reported_without_success_notification(self):
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock,
                               side_effect=passcode.PasscodeError("パスコードが保存されていません")):
            status, result = self.request(body={"enabled": True})
            self.assertEqual(status, 409)
            self.assertIn("保存されていません", result["error"])
        self.manager.notify.assert_not_called()

    def test_removed_other_prompts_scope_cannot_change_configuration(self):
        with mock.patch.object(passcode, "configure_automatic", new_callable=mock.AsyncMock,
                               return_value={"ok": True}) as configure:
            for value in (True, False, "true", 1, None, [], {}):
                status, _ = self.request(body={"enabled": True, "other_prompts": value})
                self.assertEqual(status, 400)
            configure.assert_not_awaited()
        self.manager.require.assert_not_called()

    def test_status_only_returns_flags_and_binds_to_current_ready_device(self):
        automatic = {"enabled": True, "blocked": True, "device_matches": True}
        with mock.patch.object(passcode, "available", return_value=True), \
                mock.patch.object(passcode, "is_saved", return_value=True), \
                mock.patch.object(passcode, "auto_status", return_value=automatic) as status:
            code, result = self.request(method="GET")
            self.assertEqual(code, 200)
            self.assertEqual(result, {"available": True, "saved": True, "automatic": automatic})
            status.assert_called_once_with("test-iphone")
            status.reset_mock()
            self.session.ready = False
            self.request(method="GET")
            status.assert_called_once_with(None)

    def test_manual_entry_is_xctest_only_and_old_confirmation_flags_are_refused(self):
        with mock.patch.object(passcode, "enter", new_callable=mock.AsyncMock,
                               return_value={"ok": True}) as enter:
            for value in (True, False, "true", 1, None, [], {}):
                status, _ = self.request(action="enter", body={"confirmed_on_device": value})
                self.assertEqual(status, 400)
                status, _ = self.request(action="enter", body={"confirmed_on_device": True,
                                                              "open_lock_screen": value})
                self.assertEqual(status, 400)
            status, _ = self.request(action="enter", body={"confirmed_on_device": True},
                                     headers={"user-agent": "utsushite-mcp"})
            self.assertEqual(status, 403)
            enter.assert_not_awaited()
            status, _ = self.request(action="enter", body={})
            self.assertEqual(status, 200)
            enter.assert_awaited_once_with(self.session)

    def test_save_and_delete_refresh_other_open_windows(self):
        for action in ("save", "delete"):
            self.manager.notify.reset_mock()
            with mock.patch.object(passcode, action, new_callable=mock.AsyncMock):
                code, _ = self.request(action=action, body={"passcode": "test-only"})
                self.assertEqual(code, 200)
            self.manager.notify.assert_called_once_with({"t": "passcode"})


class McpPasscodeBoundaryTests(unittest.TestCase):
    def test_no_passcode_or_automatic_configuration_tool_is_exposed(self):
        self.assertFalse(any("passcode" in tool["name"] for tool in mcp.TOOLS))
        client = mock.Mock()
        for name in ("utsushite_passcode", "utsushite_passcode_automatic"):
            with self.assertRaises(KeyError):
                mcp.handle_tool(client, name, {"enabled": True})
        client.call.assert_not_called()

    def test_handshake_prioritizes_elements_and_explains_preapproved_startup(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(message).encode()), encoding="utf-8")
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(mcp.Client, "call", return_value={}):
            mcp.run(1)
            stdout.flush()
            reply = json.loads(stdout.buffer.getvalue())
        instructions = reply["result"]["instructions"]
        self.assertLess(instructions.index("utsushite_elements"), instructions.index("utsushite_screenshot"))
        self.assertIn("事前に有効", instructions)
        self.assertNotIn("毎回スクリーンショット", instructions)


if __name__ == "__main__":
    unittest.main()
