"""Start the iCtrl server for an iCtrl screen that lost it.

A page cannot start a process: when the server is gone (stopped by hand,
crashed), an iCtrl screen left open in the agent's browser could only show a
message. The MCP process lives as long as the agent session, so it offers one
thing on 127.0.0.1:<server port + 1>: POST /start from the iCtrl page itself
(its Origin is the server's own) runs ensure_server. Nothing else is served;
no registry entry, no resident program.

Several agent sessions each run an MCP process; the first to bind the port
offers it, and another takes over at its next tool call once the port is free.
"""
from __future__ import annotations

import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


class _ExclusiveServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket bind the same port; one
    # MCP process at a time must own it.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def starter_port(port: int) -> int:
    return port + 1


def make_handler(port: int, start):
    """The request handler: POST /start from the iCtrl page runs start()."""
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{starter_port(port)}", f"localhost:{starter_port(port)}"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            origin = self.headers.get("Origin")
            if self.path != "/start" or origin not in origins or self.headers.get("Host") not in hosts:
                self.send_response(403)
                self.end_headers()
                return
            try:
                start()
                status = 204
            except BaseException as error:   # ensure_server raises SystemExit
                log.warning("starting the server for the page failed: %s", error)
                status = 500
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):   # stdout/stderr belong to MCP
            pass

    return Handler


def offer(port: int, start) -> bool:
    """Listen for the iCtrl page's start request unless already listening.
    start() starts the server (ensure_server). Returns whether this process
    offers it now."""
    global _server
    with _lock:
        if _server is not None:
            return True
        handler = make_handler(port, start)
        try:
            _server = _ExclusiveServer(("127.0.0.1", starter_port(port)), handler)
        except OSError:
            return False                    # another session offers it
        threading.Thread(target=_server.serve_forever, name="ictrl-starter", daemon=True).start()
        return True
