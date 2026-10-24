"""Notice when someone touches the iPhone itself, to give back its keyboard.

While iCtrl's virtual hardware keyboard is connected, iOS hides the
on-screen keyboard; a finger on the real screen should bring it back.

The touch is taken from SpringBoard's info-level log ("Sending UIEvent type:
0" = touches, logged for every touch because SpringBoard's system-gesture
window sees them all). A touch that iCtrl itself injected moments before is
not a physical one, so those are skipped by time.

backboardd's debug-level "Multitouch event" lines also tell real fingers
apart (they carry contact radii), but reading them keeps debug logging on in
the process that delivers every touch. The info-level reading is lighter and
sees touches in apps and on the home screen.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

_TOUCH_EVENT = re.compile(r"Sending UIEvent type: 0;")
# SpringBoard, every time the screen turns, also inside an app (iOS 27): "Active interface orientation changing from portrait (1) to
# landscapeRight (3)  (message: 'SBTraitArbiterPipelineManager')". The
# "[Interface Orientation] was:… now:…" line appears only when an app comes
# to the front, not when the phone is turned within it.
# SpringBoard's recording indicator, with the set of sensors in use, e.g.
# "[Recording Indicator] Active camera/microphone activity changed:\n{(\n
#  <SBSensorActivityAttribution: …; bundleIdentifier: com.apple.camera; …;
#  sensor: camera>\n)}"; an empty set "{(\n)}" when nothing is in use.
_SENSORS_CHANGED = "[Recording Indicator] Active camera/microphone activity changed:"
_INTERFACE_ORIENTATION = re.compile(r"Active interface orientation changing from \w+ \(\d\) to (\w+) \(\d\)")
_GESTURE_STUCK = "Failed to receive system gesture state notification"
INFO_FLAGS = 0x104          # PAYLOAD | INFO: no debug level, no history
INJECTED_WINDOW = 0.8       # s after iCtrl's own touch during which touches are ours
DEBOUNCE = 0.5              # one notification per touch, not per phase


class PhysicalTouchWatcher:
    def __init__(self, session, on_touch: Callable[[], Awaitable[None]],
                 on_orientation: Callable[[str], None] = lambda _name: None,
                 on_camera: Callable[[bool], None] = lambda _in_use: None):
        self.session = session
        self.on_touch = on_touch
        self.on_orientation = on_orientation
        self.on_camera = on_camera
        self._task: Optional[asyncio.Task] = None
        self.touches = 0
        self._last = 0.0
        self._last_stuck = 0.0

    async def start(self) -> bool:
        pid = await self._pid("SpringBoard")
        if pid is None:
            log.warning("physical touch detection unavailable (SpringBoard not found)")
            return False
        self._task = asyncio.create_task(self._run(pid), name="ictrl-physical-touch")
        log.info("watching for physical touches (SpringBoard pid %d, info level)", pid)
        return True

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _pid(self, name: str) -> Optional[int]:
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        with contextlib.suppress(Exception):
            async with AppServiceService(self.session.rsd) as svc:
                for proc in await asyncio.wait_for(svc.list_processes(), 10):
                    url = str(proc.get("executableURL", {}).get("relative", "") or proc.get("executableURL", ""))
                    if url.endswith("/" + name):
                        return int(proc["processIdentifier"])
        return None

    async def _run(self, pid: int) -> None:
        from pymobiledevice3.services.os_trace import OsTraceService
        try:
            async for entry in OsTraceService(self.session.rsd).syslog(pid=pid, stream_flags=INFO_FLAGS):
                message = getattr(entry, "message", "") or ""
                if message.startswith(_SENSORS_CHANGED):
                    with contextlib.suppress(Exception):
                        self.on_camera("sensor: camera>" in message)
                    continue
                turned = _INTERFACE_ORIENTATION.search(message)
                if turned:
                    with contextlib.suppress(Exception):
                        self.on_orientation(turned.group(1))
                    continue
                if _GESTURE_STUCK in message:
                    # Seen while home-screen swipes stopped working
                    # (cause not settled): keep the moment on record.
                    now = time.monotonic()
                    if now - self._last_stuck > 10:
                        self._last_stuck = now
                        log.warning("SpringBoard: system gesture state late (swipes may fail): %s", message[:160])
                    continue
                if not _TOUCH_EVENT.search(message):
                    continue
                now = time.monotonic()
                if now - getattr(self.session, "last_injected_touch", 0.0) < INJECTED_WINDOW:
                    continue                    # iCtrl's own touch
                if now - self._last < DEBOUNCE:
                    continue
                self._last = now
                self.touches += 1
                # Diagnostics: what was taken for a finger on the phone, and
                # how long after iCtrl's last keyboard event (no text content).
                key_at = getattr(self.session, "last_key_event", 0.0)
                log.info("physical touch seen: %s (%.0f ms after iCtrl's last key event)",
                         message[:160], (now - key_at) * 1000 if key_at else -1)
                with contextlib.suppress(Exception):
                    await self.on_touch()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("physical touch stream ended: %r", error)
