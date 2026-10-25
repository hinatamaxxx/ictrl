"""Connect device services straight to the userspace TCP stack, in process.

pymobiledevice3 bridges every service connection through a local relay: on
Windows a 127.0.0.1 TCP listener that first reads a 2-byte destination port.
Observed over Wi-Fi: dial() wrote that header, yet the relay's
stream began with the following "PRI * HTTP/2.0" preface, so it connected to
port 0x5052 ("PR"). The phone answered with RST and the RemoteXPC handshake
waited out its timeout. The two bytes were lost between the two ends of a
local Windows (Proactor) socket pair — the relay's peer was this process,
not a filter.

Here an asyncio Transport is laid directly over the pytcp socket, so no OS
socket, header or relay exists for a byte to get lost in. It is also one hop
shorter for every input event.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

_CHUNK = 64 * 1024
_HIGH_WATER = 256 * 1024
_LOW_WATER = 64 * 1024


class _NoOsSocket:
    """ServiceConnection insists on a ``socket`` extra; it only touches it in
    its synchronous helpers (unused here) and to close it. Nothing exists to
    close: the stream is in-process."""

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        pass

    def setblocking(self, flag) -> None:
        pass

    def __getattr__(self, name):
        raise OSError(f"in-process stream has no OS socket ({name})")


class PytcpTransport(asyncio.Transport):
    def __init__(self, sock, protocol: asyncio.Protocol, peer: tuple[str, int]):
        super().__init__({"peername": peer, "socket": _NoOsSocket()})
        self._sock = sock
        self._protocol = protocol
        self._loop = asyncio.get_running_loop()
        self._outbox: list[bytes] = []
        self._buffered = 0
        self._wakeup = asyncio.Event()
        self._closing = False
        self._eof_requested = False
        self._service_port = peer[1]
        self._close_reason: Optional[str] = None
        self._close_error_type: Optional[str] = None
        self._close_logged = False
        self._writing_paused = False
        self._reading_paused = False
        self._read_allowed = asyncio.Event()
        self._read_allowed.set()
        self._sender = self._loop.create_task(self._send_loop(), name="utsushite-pytcp-send")
        self._receiver = self._loop.create_task(self._recv_loop(), name="utsushite-pytcp-recv")

    # -- reading ----------------------------------------------------------
    async def _recv_loop(self) -> None:
        try:
            while True:
                await self._read_allowed.wait()
                data = await self._sock.recv(_CHUNK)
                if not data:
                    self._remember_close("peer EOF")
                    break
                self._protocol.data_received(data)
            keep_open = self._protocol.eof_received()
            if not keep_open:
                self.close()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._force_close(error, "receive failed")

    def pause_reading(self) -> None:
        self._reading_paused = True
        self._read_allowed.clear()

    def resume_reading(self) -> None:
        self._reading_paused = False
        self._read_allowed.set()

    def is_reading(self) -> bool:
        return not self._reading_paused and not self._closing

    # -- writing ----------------------------------------------------------
    def write(self, data) -> None:
        if not data:
            return
        if self._closing or self._eof_requested:
            self._log_close()
            # A closed device service is an input-channel failure. RuntimeError
            # made DeviceSession keep reporting ready after this write failed.
            raise ConnectionError(f"device service transport is closing ({self._close_reason})")
        self._outbox.append(bytes(data))
        self._buffered += len(data)
        self._wakeup.set()
        if not self._writing_paused and self._buffered > _HIGH_WATER:
            self._writing_paused = True
            self._protocol.pause_writing()

    async def _send_loop(self) -> None:
        try:
            while True:
                await self._wakeup.wait()
                self._wakeup.clear()
                while self._outbox:
                    data = self._outbox.pop(0)
                    view = memoryview(data)
                    while view:
                        sent = await self._sock.send(bytes(view))
                        view = view[sent if sent else len(view):]
                    self._buffered -= len(data)
                    if self._writing_paused and self._buffered <= _LOW_WATER:
                        self._writing_paused = False
                        self._protocol.resume_writing()
                if self._eof_requested and not self._outbox:
                    with contextlib.suppress(Exception):
                        from pmd_pytcp.socket import SHUT_WR
                        self._sock.shutdown(SHUT_WR)
                if self._closing and not self._outbox:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._force_close(error, "send failed")
            return
        self._finish(None)

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        self._remember_close("local write EOF")
        self._eof_requested = True
        self._wakeup.set()

    def get_write_buffer_size(self) -> int:
        return self._buffered

    def get_write_buffer_limits(self) -> tuple[int, int]:
        return _LOW_WATER, _HIGH_WATER

    def set_write_buffer_limits(self, high=None, low=None) -> None:
        pass

    # -- lifecycle --------------------------------------------------------
    def is_closing(self) -> bool:
        return self._closing

    def get_protocol(self):
        return self._protocol

    def set_protocol(self, protocol) -> None:
        self._protocol = protocol

    def close(self) -> None:
        """Flush pending writes, then close (like a socket transport)."""
        if self._closing:
            return
        self._remember_close("local close")
        self._closing = True
        self._wakeup.set()

    def abort(self) -> None:
        self._force_close(None, "local abort")

    def _remember_close(self, reason: str, error: Optional[BaseException] = None) -> None:
        if self._close_reason is None:
            self._close_reason = reason
            self._close_error_type = type(error).__name__ if error else None

    def _log_close(self) -> None:
        if not self._close_logged:
            self._close_logged = True
            # Port and event identify the failed service stream, without its
            # address, input payload, or possibly sensitive exception message.
            log.warning("device service transport ended: port=%s reason=%s error_type=%s",
                        self._service_port, self._close_reason, self._close_error_type)

    def _force_close(self, error: Optional[BaseException], reason: str) -> None:
        self._remember_close(reason, error)
        if error is not None:
            self._log_close()
        self._closing = True
        self._outbox.clear()
        self._finish(error)

    def _finish(self, error: Optional[BaseException]) -> None:
        if getattr(self, "_finished", False):
            return
        self._finished = True
        current = asyncio.current_task()
        for task in (self._sender, self._receiver):
            if task is not current:
                task.cancel()
        with contextlib.suppress(Exception):
            self._sock.close()
        self._loop.call_soon(self._protocol.connection_lost, error)


def install() -> None:
    """Route pymobiledevice3's userspace dial plane through PytcpTransport."""
    from pymobiledevice3.remote import userspace_tunnel as ut
    cls = ut.UserspaceDialPlane
    if getattr(cls.dial, "_utsushite_direct", False):
        return
    relay_dial = cls.dial

    async def dial(self, host: Optional[str] = None, port: Optional[int] = None, **kwargs: Any):
        if host is None or str(host) != self._device_addr or kwargs:
            return await relay_dial(self, host, port, **kwargs)
        if self._closing:
            raise ConnectionError("userspace dial plane is closed")
        sock = await self._tun.connect_tcp(self._device_addr, port)
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=2 ** 16, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        transport = PytcpTransport(sock, protocol, (self._device_addr, port))
        protocol.connection_made(transport)
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return reader, writer

    dial._utsushite_direct = True
    cls.dial = dial
