"""Host-side identity and ownership.

Two observed behaviours drive this module:

* The Windows Apple Mobile Device service never answered a usbmux ``Connect``
  carrying pymobiledevice3's default client identity, while the identical
  request with another ProgName/ClientVersionString succeeded at once. We
  always introduce ourselves under our own name.
* Two processes that each build a CoreDevice tunnel to the same iPhone make
  the phone tear one of them down. A named mutex per device makes the owner
  explicit; a second owner fails immediately instead of waiting or retrying.
"""
from __future__ import annotations

import hashlib

from pymobiledevice3 import usbmux

PROG_NAME = "iCtrl"
CLIENT_VERSION = "iCtrl-1"


def install_usbmux_identity() -> None:
    cls = usbmux.PlistMuxConnection
    if getattr(cls._send, "_ictrl_identity", False):
        return
    original = cls._send

    async def _send(self, data):
        return await original(self, {"ProgName": PROG_NAME,
                                     "ClientVersionString": CLIENT_VERSION, **data})

    _send._ictrl_identity = True
    cls._send = _send


class DeviceBusy(ConnectionError):
    pass


BUSY_MESSAGE = ("このiPhoneは別のプロセスが操作中です。"
                "他の操作アプリやテストを終了してから接続してください。")

_WAIT_ABANDONED = 0x80


def _mutex_name(udid: str) -> str:
    # The same name space as the earlier iPhoneMirror control tools, so the
    # two exclude each other when both are installed.
    digest = hashlib.sha256(udid.encode("utf-8")).hexdigest()[:24]
    return "Local\\iPhoneMirror-CoreDevice-" + digest


class DeviceOwnership:
    """Windows named mutex. Must be acquired and released on the same thread."""

    _held: set[str] = set()

    def __init__(self, udid: str):
        self.name = _mutex_name(udid)
        self._handle = None

    def acquire(self) -> None:
        import win32event
        if self.name in self._held:
            raise DeviceBusy(BUSY_MESSAGE)
        handle = win32event.CreateMutex(None, False, self.name)
        result = win32event.WaitForSingleObject(handle, 0)
        if result not in (win32event.WAIT_OBJECT_0, _WAIT_ABANDONED):
            handle.Close()
            raise DeviceBusy(BUSY_MESSAGE)
        self._handle = handle
        self._held.add(self.name)

    def release(self) -> None:
        import win32event
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            win32event.ReleaseMutex(handle)
        finally:
            self._held.discard(self.name)
            handle.Close()
