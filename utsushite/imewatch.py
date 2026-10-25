"""Follow the Windows IME (the taskbar's あ / A) without polling.

Windows itself tells when it may have changed: the text services framework
(TSF, "Cicero") raises WinEvent 0x7FFFFF30 on its CicMarshalWndClass window
when the IME state changes (about 3 ms after 半角/全角,
with the new state already readable), and EVENT_SYSTEM_FOREGROUND tells
that another window (with its own IME state) came to the front. A thread of
the server receives both (out-of-context WinEvents: Windows does not wait for
the receiver, so nothing here can slow the keyboard) and each one leads to a
single read of the IME (winime.state) on the server's loop.

Asking at intervals would deliver a change up to an interval late, and a
low-level keyboard hook reads before the IME has applied the key.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_TSF_STATE = 0x7FFFFF30          # raised by TSF (CicMarshalWndClass) on IME state changes
WM_QUIT = 0x0012

WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                  wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


class ImeWatcher:
    def __init__(self, on_change: Callable[[bool], Awaitable[None]]):
        self.on_change = on_change
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread_id = 0
        self._queued = False          # several notices close together make one read
        self._last: Optional[bool] = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._listen, name="utsushite-ime-watch", daemon=True).start()
        self.notice()                 # the state at start

    async def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def notice(self) -> None:
        """On the loop: read once, unless a read is already queued. Also called
        when a page saw an IME key: the Claude app (Electron) raises no TSF
        notice for its own IME."""
        if self._queued:
            return
        self._queued = True
        self._loop.create_task(self._read())

    async def _read(self) -> None:
        from . import winime
        self._queued = False
        try:
            state = await asyncio.to_thread(winime.state)
        except Exception as error:
            log.info("windows ime: not readable (%r)", error)
            return
        if not state.get("available"):
            return
        japanese = bool(state["japanese"])
        if japanese != self._last:
            self._last = japanese
            with contextlib.suppress(Exception):
                await self.on_change(japanese)

    def _listen(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        user32.SetWinEventHook.restype = wintypes.HANDLE
        self._thread_id = kernel32.GetCurrentThreadId()

        def notice(*_args):
            try:
                self._loop.call_soon_threadsafe(self.notice)
            except RuntimeError:
                pass                  # the loop has closed

        proc = WINEVENTPROC(notice)   # kept alive by this frame
        hooks = [user32.SetWinEventHook(EVENT_TSF_STATE, EVENT_TSF_STATE, None, proc, 0, 0, 0),
                 user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, proc, 0, 0, 0)]
        if all(hooks):
            log.info("IME watch: following Windows' IME notices and the window in front")
        else:
            log.warning("IME watch: event hooks not installed (%d)", ctypes.get_last_error())
        msg = wintypes.MSG()          # out-of-context WinEvents arrive through this thread's messages
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))
        for hook in hooks:
            if hook:
                user32.UnhookWinEvent(hook)
