"""Local API over a Windows named pipe, for the MCP server and other agents.

Loopback TCP is not reliable on every PC: a network filter such as AdGuard
with localhost filtering (filterLocalhost) proxies 127.0.0.1 connections of
some executables. Through it, large HTTP responses from the iCtrl server to
Python 3.13 were reset or stalled mid-body, while curl and the Store
Python 3.12 received them intact. A named pipe is not a network connection, so no network filter
sits in between.

Protocol: one request per connection. The client writes one JSON line
{"method", "path", "body"} and reads one JSON line
{"status", "headers", "body_b64"}. The request is answered by the very same
ASGI app that serves HTTP, in process.
"""
from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import json
import logging
import threading
from typing import Any, Optional

log = logging.getLogger(__name__)

_BUF = 1 << 20


def pipe_name() -> str:
    user = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:12]
    return rf"\\.\pipe\ictrl-{user}"


async def call_asgi(app, method: str, path: str, body: Optional[bytes]) -> dict:
    target, _, query = path.partition("?")
    payload = body or b""
    headers = [(b"host", b"127.0.0.1"), (b"content-length", str(len(payload)).encode())]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": target, "raw_path": target.encode(),
        "query_string": query.encode(), "headers": headers,
        "client": ("pipe", 0), "server": ("127.0.0.1", 0), "root_path": "",
    }
    sent = False
    status, out_headers, chunks = 500, {}, []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await asyncio.Event().wait()  # no disconnect during a single exchange

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            for k, v in message.get("headers", []):
                out_headers[k.decode("latin-1")] = v.decode("latin-1")
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return {"status": status, "headers": out_headers,
            "body_b64": base64.b64encode(b"".join(chunks)).decode("ascii")}


class PipeServer:
    """Accepts named-pipe clients on a thread; answers on the event loop."""

    def __init__(self, app, loop: asyncio.AbstractEventLoop):
        self.app = app
        self.loop = loop
        self.name = pipe_name()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="ictrl-pipe", daemon=True)

    def start(self) -> None:
        self._thread.start()
        log.info("pipe API at %s", self.name)

    def stop(self) -> None:
        self._stop.set()
        # Unblock ConnectNamedPipe with a throwaway client.
        try:
            import win32file
            h = win32file.CreateFile(self.name, 0xC0000000, 0, None, 3, 0, None)
            h.Close()
        except Exception:
            pass

    def _serve(self) -> None:
        import pywintypes
        import win32pipe
        while not self._stop.is_set():
            try:
                handle = win32pipe.CreateNamedPipe(
                    self.name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT
                    | getattr(win32pipe, "PIPE_REJECT_REMOTE_CLIENTS", 0x8),
                    win32pipe.PIPE_UNLIMITED_INSTANCES, _BUF, _BUF, 0, None)
                try:
                    win32pipe.ConnectNamedPipe(handle, None)
                except pywintypes.error as error:
                    if error.winerror != 535:  # ERROR_PIPE_CONNECTED
                        raise
            except Exception:
                log.exception("pipe accept failed")
                return
            if self._stop.is_set():
                handle.Close()
                return
            threading.Thread(target=self._client, args=(handle,), daemon=True).start()

    def _client(self, handle) -> None:
        import win32file
        import win32pipe
        try:
            data = b""
            while not data.endswith(b"\n"):
                _, chunk = win32file.ReadFile(handle, 65536)
                if not chunk:
                    return
                data += chunk
            request = json.loads(data)
            body = request.get("body")
            raw = json.dumps(body).encode() if body is not None else None
            future = asyncio.run_coroutine_threadsafe(
                call_asgi(self.app, request.get("method", "GET"), request["path"], raw), self.loop)
            response = future.result(timeout=float(request.get("timeout", 120)))
            win32file.WriteFile(handle, json.dumps(response).encode() + b"\n")
            win32file.FlushFileBuffers(handle)
        except Exception as error:
            log.warning("pipe request failed: %r", error)
            try:
                win32file.WriteFile(handle, json.dumps(
                    {"status": 500, "headers": {}, "body_b64": base64.b64encode(
                        json.dumps({"ok": False, "error": str(error)}).encode()).decode()}).encode() + b"\n")
            except Exception:
                pass
        finally:
            try:
                win32pipe.DisconnectNamedPipe(handle)
            except Exception:
                pass
            handle.Close()


def request(method: str, path: str, body: Any = None, timeout: float = 120) -> tuple[int, dict, bytes]:
    """Client side: one request over the pipe. Raises FileNotFoundError when
    no server is listening."""
    import pywintypes
    import win32file
    import win32pipe
    try:
        handle = win32file.CreateFile(pipe_name(), 0xC0000000, 0, None, 3, 0, None)
    except pywintypes.error as error:
        if error.winerror in (2, 231):  # not found / all instances busy
            if error.winerror == 231:
                win32pipe.WaitNamedPipe(pipe_name(), 2000)
                handle = win32file.CreateFile(pipe_name(), 0xC0000000, 0, None, 3, 0, None)
            else:
                raise FileNotFoundError(pipe_name()) from error
        else:
            raise
    try:
        message = {"method": method, "path": path, "timeout": timeout}
        if body is not None:
            message["body"] = body
        win32file.WriteFile(handle, json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            _, chunk = win32file.ReadFile(handle, _BUF)
            if not chunk:
                break
            data += chunk
    finally:
        handle.Close()
    response = json.loads(data)
    return response["status"], response["headers"], base64.b64decode(response["body_b64"])
