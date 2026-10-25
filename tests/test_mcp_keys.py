import io
import json
import sys
import unittest
from unittest import mock

from utsushite import keys, mcp


class KeysTests(unittest.TestCase):
    def test_layout_safe_typing(self):
        self.assertTrue(keys.is_typable("Hello world 123-,./"))
        # Symbols differ between US and JIS layouts; those are pasted instead.
        self.assertFalse(keys.is_typable("a@b"))
        self.assertFalse(keys.is_typable("日本語"))

    def test_named_keys(self):
        self.assertEqual(keys.usage_for_name("Enter"), 0x28)
        self.assertEqual(keys.usage_for_name("a"), 0x04)
        with self.assertRaises(ValueError):
            keys.usage_for_name("nope")


class McpTests(unittest.TestCase):
    def run_session(self, *messages, call_error=None):
        stdin = io.TextIOWrapper(io.BytesIO("\n".join(json.dumps(m) for m in messages).encode()), encoding="utf-8")
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        # Even a handshake ends with automation/stop on EOF; never let this
        # unit test contact the running utsushite server or the user's iPhone.
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(mcp.Client, "call", return_value={}, side_effect=call_error):
            mcp.run(1)
            stdout.flush()
            return [json.loads(l) for l in stdout.buffer.getvalue().decode().splitlines()]

    def test_handshake_and_tool_list(self):
        replies = self.run_session(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "bogus"})
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "utsushite")
        names = {t["name"] for t in replies[1]["result"]["tools"]}
        self.assertIn("utsushite_screenshot", names)
        self.assertEqual(replies[2]["error"]["code"], -32601)

    def test_tool_error_is_reported_not_raised(self):
        replies = self.run_session({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "utsushite_tap", "arguments": {"x": 0.5, "y": 0.5}}},
                                   call_error=RuntimeError("iPhoneに接続されていません"))
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("接続されていません", replies[0]["result"]["content"][0]["text"])

    def test_pixel_coordinates_use_last_screenshot_size(self):
        client = mcp.Client(1)
        self.assertEqual(client.coords({"x": 0.5, "y": 0.25}, "x", "y"), {"x": 0.5, "y": 0.25})
        client.shot_size = (471, 1024)
        self.assertEqual(client.coords({"x": 100, "y": 200}, "x", "y"),
                         {"x": 100.0, "y": 200.0, "width": 471, "height": 1024})


if __name__ == "__main__":
    unittest.main()
