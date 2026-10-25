"""The MCP process starts the iCtrl server for the iCtrl page only (starter.py).

The handler is driven with an in-memory request: loopback TCP on a PC with a
localhost-filtering proxy (AdGuard) resets some connections by itself."""
import io
import socket
import unittest

from ictrl import starter

PORT = 47800


class FakeConnection:
    """What BaseHTTPRequestHandler needs from a socket."""
    def __init__(self, raw: bytes):
        self.raw = raw
        self.sent = b""

    def makefile(self, mode, *args, **kwargs):
        return io.BytesIO(self.raw)

    def sendall(self, data):
        self.sent += bytes(data)

    def settimeout(self, _):
        pass


def ask(origin, path="/start", host=f"127.0.0.1:{PORT + 1}", start=lambda: None) -> int:
    lines = [f"POST {path} HTTP/1.1", f"Host: {host}", "Content-Length: 0"]
    if origin:
        lines.append(f"Origin: {origin}")
    conn = FakeConnection(("\r\n".join(lines) + "\r\n\r\n").encode())
    starter.make_handler(PORT, start)(conn, ("127.0.0.1", 50000), None)
    return int(conn.sent.split(b" ", 2)[1])


class StarterTests(unittest.TestCase):
    def test_the_ictrl_page_starts_the_server(self):
        started = []
        self.assertEqual(ask(f"http://127.0.0.1:{PORT}", start=lambda: started.append(True)), 204)
        self.assertEqual(ask(f"http://localhost:{PORT}", start=lambda: started.append(True)), 204)
        self.assertEqual(started, [True, True])

    def test_other_pages_and_tools_are_refused(self):
        started = []
        start = lambda: started.append(True)
        self.assertEqual(ask("https://example.com", start=start), 403)
        self.assertEqual(ask(f"http://127.0.0.1:{PORT + 5}", start=start), 403)
        self.assertEqual(ask(None, start=start), 403)
        self.assertEqual(ask(f"http://127.0.0.1:{PORT}", path="/other", start=start), 403)
        self.assertEqual(ask(f"http://127.0.0.1:{PORT}", host="evil.test", start=start), 403)
        self.assertEqual(started, [])

    def test_a_failed_start_is_reported(self):
        def fail():
            raise SystemExit("could not start")
        self.assertEqual(ask(f"http://127.0.0.1:{PORT}", start=fail), 500)


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.tearDown()          # mcp.run in other tests leaves its starter open

    def tearDown(self):
        if starter._server is not None:
            starter._server.shutdown()
            starter._server.server_close()
            starter._server = None

    def test_only_one_process_offers_it(self):
        with socket.socket() as other:      # another session's MCP holds the port
            other.bind(("127.0.0.1", 0))
            other.listen()
            port = other.getsockname()[1] - 1
            self.assertFalse(starter.offer(port, lambda: None))

    def test_it_listens_when_the_port_is_free(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1] - 1
        self.assertTrue(starter.offer(port, lambda: None))
        self.assertTrue(starter.offer(port, lambda: None))   # already offering


if __name__ == "__main__":
    unittest.main()
