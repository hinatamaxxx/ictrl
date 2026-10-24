import asyncio
import json
import types
import unittest
from unittest import mock

from starlette.websockets import WebSocketDisconnect

from ictrl import server


class ViewerDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def diagnostic(self, message):
        manager = types.SimpleNamespace(
            session=None, listen=lambda: asyncio.Queue(), unlisten=mock.Mock(),
            window_opened=mock.Mock(), window_closed=mock.Mock(),
            page_opened=mock.Mock(), page_closed=mock.Mock())
        with mock.patch.object(server, "Manager", return_value=manager):
            app = server.create_app(47800)
        route = next(r for r in app.routes if getattr(r, "path", None) == "/ws")
        websocket = types.SimpleNamespace(
            headers={"origin": "http://127.0.0.1:47800", "user-agent": "Mozilla/5.0"},
            accept=mock.AsyncMock(), send_text=mock.AsyncMock(),
            receive_text=mock.AsyncMock(side_effect=[json.dumps(message), WebSocketDisconnect()]))
        with self.assertLogs("ictrl.server", level="INFO") as captured:
            await route.endpoint(websocket)
        return next(record for record in captured.records if record.msg == "viewer %s")

    async def test_key_diagnostic_excludes_input_but_keeps_format(self):
        record = await self.diagnostic({
            "t": "client", "mode": "key", "hw": "private-test-key",
            "format": "jp", "text": "another-private-value"})
        self.assertNotIn("hw", record.args)
        self.assertNotIn("private", record.getMessage())
        self.assertEqual(record.args["mode"], "key")
        self.assertEqual(record.args["format"], "jp")

    async def test_video_hardware_diagnostic_is_preserved(self):
        record = await self.diagnostic({
            "t": "client", "mode": "webcodecs", "hw": "prefer-hardware",
            "format": "annexb", "frame": "1080x1920", "ua": "test-browser"})
        self.assertEqual(record.args, {
            "mode": "webcodecs", "hw": "prefer-hardware", "format": "annexb",
            "frame": "1080x1920", "ua": "test-browser"})

    async def test_decoder_failure_diagnostic_is_preserved(self):
        record = await self.diagnostic({
            "t": "client", "mode": "decoder-error", "hw": "Unsupported codec"})
        self.assertEqual(record.args["hw"], "Unsupported codec")


if __name__ == "__main__":
    unittest.main()
