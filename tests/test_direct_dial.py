import asyncio
import types
import unittest
from unittest.mock import AsyncMock

from ictrl.direct_dial import PytcpTransport
from ictrl.session import DeviceSession, SessionError


class FakeSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.send_error = None
        self.closed = False

    async def recv(self, _size):
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def send(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)
        return len(data)

    def close(self):
        self.closed = True


class FakeProtocol(asyncio.Protocol):
    def __init__(self):
        self.closed = asyncio.Event()
        self.error = None
        self.eof_seen = asyncio.Event()
        self.keep_open = False

    def eof_received(self):
        self.eof_seen.set()
        return self.keep_open

    def connection_lost(self, error):
        self.error = error
        self.closed.set()


class DirectDialTests(unittest.IsolatedAsyncioTestCase):
    def make_transport(self):
        sock, protocol = FakeSocket(), FakeProtocol()
        transport = PytcpTransport(sock, protocol, ("private-device-address", 12345))

        async def cleanup():
            transport.abort()
            await asyncio.gather(transport._sender, transport._receiver, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        return transport, sock, protocol

    async def test_peer_eof_rejects_write_as_connection_failure_and_keeps_first_reason(self):
        transport, sock, protocol = self.make_transport()
        sock.incoming.put_nowait(b"")
        await asyncio.wait_for(protocol.closed.wait(), 1)
        transport.abort()
        with self.assertLogs("ictrl.direct_dial", level="WARNING") as captured:
            with self.assertRaisesRegex(ConnectionError, "peer EOF"):
                transport.write(b"private-input")
            with self.assertRaises(ConnectionError):
                transport.write(b"private-input")
        self.assertEqual(len(captured.output), 1)
        self.assertIn("port=12345 reason=peer EOF", captured.output[0])
        self.assertNotIn("private", captured.output[0])
        self.assertEqual(sock.sent, [])

    async def test_local_close_rejects_write_without_replaying_pending_input(self):
        transport, sock, protocol = self.make_transport()
        transport.write(b"already-queued")
        transport.close()
        with self.assertLogs("ictrl.direct_dial", level="WARNING"):
            with self.assertRaisesRegex(ConnectionError, "local close"):
                transport.write(b"must-not-send")
        await asyncio.wait_for(protocol.closed.wait(), 1)
        self.assertEqual(sock.sent, [b"already-queued"])

    async def test_local_write_eof_rejects_further_write(self):
        transport, sock, _ = self.make_transport()
        transport.write_eof()
        with self.assertLogs("ictrl.direct_dial", level="WARNING"):
            with self.assertRaisesRegex(ConnectionError, "local write EOF"):
                transport.write(b"must-not-send")
        self.assertEqual(sock.sent, [])

    async def test_peer_half_close_can_keep_writer_open(self):
        transport, sock, protocol = self.make_transport()
        protocol.keep_open = True
        sock.incoming.put_nowait(b"")
        await asyncio.wait_for(protocol.eof_seen.wait(), 1)
        transport.write(b"response")
        transport.close()
        await asyncio.wait_for(protocol.closed.wait(), 1)
        self.assertEqual(sock.sent, [b"response"])

    async def test_receive_failure_logs_type_without_exception_message_or_address(self):
        transport, sock, protocol = self.make_transport()
        with self.assertLogs("ictrl.direct_dial", level="WARNING") as captured:
            sock.incoming.put_nowait(OSError("private-input"))
            await asyncio.wait_for(protocol.closed.wait(), 1)
            with self.assertRaisesRegex(ConnectionError, "receive failed"):
                transport.write(b"must-not-send")
        self.assertEqual(len(captured.output), 1)
        self.assertIn("reason=receive failed error_type=OSError", captured.output[0])
        self.assertNotIn("private", captured.output[0])
        self.assertEqual(sock.sent, [])

    async def test_send_failure_logs_type_without_exception_message_or_payload(self):
        transport, sock, protocol = self.make_transport()
        sock.send_error = BrokenPipeError("private-input")
        with self.assertLogs("ictrl.direct_dial", level="WARNING") as captured:
            transport.write(b"private-input")
            await asyncio.wait_for(protocol.closed.wait(), 1)
            with self.assertRaisesRegex(ConnectionError, "send failed"):
                transport.write(b"must-not-send")
        self.assertEqual(len(captured.output), 1)
        self.assertIn("reason=send failed error_type=BrokenPipeError", captured.output[0])
        self.assertNotIn("private", captured.output[0])
        self.assertEqual(sock.sent, [])

    async def test_real_input_worker_marks_ready_session_failed_after_closed_indigo_write(self):
        transport, sock, protocol = self.make_transport()
        sock.incoming.put_nowait(b"")
        await asyncio.wait_for(protocol.closed.wait(), 1)
        states = []
        session = DeviceSession("fake-device", "wifi", lambda info: states.append(info.state))
        session.info.state = "ready"
        session.keyboard_id = 1
        session._open = AsyncMock()

        async def send_request(_message):
            transport.write(b"private-input")

        send = AsyncMock(side_effect=send_request)
        session.indigo = types.SimpleNamespace(service=types.SimpleNamespace(send_request=send))
        session._input_task = asyncio.create_task(session._input_worker())
        session._stack.push_async_callback(session._stop_input)
        self.addAsyncCleanup(session.close)
        with self.assertLogs(level="WARNING") as captured:
            future = session.submit({"op": "key", "key": "kana", "modifiers": []})
            with self.assertRaisesRegex(SessionError, "操作を送れませんでした"):
                await future
            await asyncio.wait_for(session.wait_closed(), 1)
        self.assertEqual(session.info.state, "failed")
        self.assertFalse(session.ready)
        self.assertIn("failed", states)
        # Plain words on screen; the exception (first cause) is in the log.
        self.assertIn("操作を送れなくなりました", session.info.error)
        self.assertTrue(any("ConnectionError" in line and "peer EOF" in line for line in captured.output))
        self.assertEqual(send.await_count, 1)
        self.assertEqual(sock.sent, [])
        session._open.assert_not_awaited()
        self.assertNotIn("private-input", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
