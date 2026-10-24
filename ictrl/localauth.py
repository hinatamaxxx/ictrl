"""Only the Windows user running iCtrl may use its local HTTP/WebSocket API.

127.0.0.1:47800 is reachable by every process on the PC, including those of
other signed-in users. For each new connection the owning process of the
client's TCP endpoint is looked up in Windows' TCP table and its user (token
SID) compared with ours. The named pipe used by MCP is already limited by
its default ACL (others may only read).
"""
from __future__ import annotations

import ctypes
import logging
import socket
import struct
import time
from ctypes import wintypes
from typing import Optional

log = logging.getLogger(__name__)

_TCP_TABLE_OWNER_PID_ALL = 5
_CACHE_SECONDS = 30.0
_cache: dict[int, tuple[bool, float]] = {}
_own_sid: Optional[str] = None


def _sid_of(pid: int) -> Optional[str]:
    import win32api
    import win32con
    import win32security
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        return None                       # another user's process (access denied) or gone


def _own() -> Optional[str]:
    global _own_sid
    if _own_sid is None:
        import os
        _own_sid = _sid_of(os.getpid())
    return _own_sid


def _pid_for(local_port: int, remote_port: int) -> Optional[int]:
    """Owner of the client end 127.0.0.1:local_port -> 127.0.0.1:remote_port."""
    iphlpapi = ctypes.WinDLL("iphlpapi")
    size = wintypes.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, socket.AF_INET, _TCP_TABLE_OWNER_PID_ALL, 0)
    buf = ctypes.create_string_buffer(size.value + 4096)
    size = wintypes.DWORD(len(buf))
    if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, socket.AF_INET,
                                    _TCP_TABLE_OWNER_PID_ALL, 0) != 0:
        return None
    count = struct.unpack_from("<I", buf, 0)[0]
    for i in range(count):
        # MIB_TCPROW_OWNER_PID: state, localAddr, localPort, remoteAddr, remotePort, pid
        state, laddr, lport, raddr, rport, pid = struct.unpack_from("<IIIIII", buf, 4 + i * 24)
        if socket.ntohs(lport & 0xFFFF) == local_port and socket.ntohs(rport & 0xFFFF) == remote_port:
            return pid
    return None


def allowed(client_port: int, server_port: int) -> bool:
    now = time.monotonic()
    hit = _cache.get(client_port)
    if hit and now - hit[1] < _CACHE_SECONDS:
        return hit[0]
    pid = _pid_for(client_port, server_port)
    ok = pid is not None and _sid_of(pid) == _own() and _own() is not None
    if not ok:
        log.warning("refused a local connection from another user's process (pid %s)", pid)
    _cache[client_port] = (ok, now)
    if len(_cache) > 512:
        _cache.clear()
    return ok


class SameUserMiddleware:
    """ASGI middleware: refuse HTTP/WebSocket from other Windows users."""

    def __init__(self, app, server_port: int):
        self.app = app
        self.server_port = server_port

    async def __call__(self, scope, receive, send):
        client = scope.get("client")
        if scope["type"] in ("http", "websocket") and client and client[0] in ("127.0.0.1", "::1"):
            if not allowed(int(client[1]), self.server_port):
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await send({"type": "http.response.body", "body": "forbidden".encode()})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
