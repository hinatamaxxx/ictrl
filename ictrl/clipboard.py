"""iPhone pasteboard watcher and the copy history shared by GUI, API and MCP.

Copies are noticed through the device's own `com.apple.pasteboard.notify.changed`
notification (pymobiledevice3's PasteboardMonitor), not by polling.

pymobiledevice3's monitor reconnects its notification channel every 2 s for
as long as it runs. A similar unbounded retry once failed ~19,000 times
against a dead tunnel on this project, so here a failed channel is reopened
at most a few times in a row; after that the watcher stops and says so. It is
also stopped with the session, so it can never outlive the tunnel.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from pymobiledevice3.remote.core_device import pasteboard_service as _pb
from pymobiledevice3.remote.core_device.pasteboard_service import (
    PASTEBOARD_CHANGED_NOTIFICATION, POLICY_ALL_PROMISED, POLICY_MATCH_SOURCE,
    POLICY_PROMISE_SECONDARY, PasteboardContent, PasteboardMonitor, PasteboardService, _change_id)
from pymobiledevice3.services.notification_proxy import NotificationProxyService

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
RESOLVE_TIMEOUT = 75.0  # Gmail copies took ~64 s to resolve the first time
STRIP_CHARS = chr(0xFFFC) + " " + chr(9) + chr(13) + chr(10)
_TEXT_OR_IMAGE = {"public.utf8-plain-text", "public.plain-text", "public.text", "public.url",
                  "public.png", "public.jpeg", "public.heic", "public.tiff", "com.compuserve.gif"}


RESOLVE_ENABLED = [True]   # diagnostics can pause automatic resolution
# Set by the session: runs the iPhone Shortcut "ictrlcopy" (see trigger_plain_copy).
PLAIN_COPY_TRIGGER: list = [None]
PLAIN_COPY_FAILED: list = [None]   # called when the shortcut did not answer
SHORTCUT_WAIT = 5.0
SHORTCUT_UTI = "link.contentkit.name"      # present on copies made by Shortcuts
SHORTCUT_RESOLVE_TIMEOUT = 5.0
_last_trigger = [0.0]
# After a promise read times out the daemon keeps working on it for up to
# ~64 s and answers nobody meanwhile; do not queue more requests until then.
DAEMON_BUSY_SECONDS = 65.0
_busy_until = [0.0]
PLAIN_COPY_PENDING: list = [None]  # called when the slow (shortcut) path starts


def daemon_busy() -> bool:
    return time.monotonic() < _busy_until[0]


async def _resolve_text(rsd, pasteboard_name, types, timeout):
    uti = next((u for u in ("public.utf8-plain-text", "public.plain-text", "public.text") if u in types), None)
    if uti is None:
        return None
    started = time.monotonic()
    try:
        data = await asyncio.wait_for(resolve(rsd, pasteboard_name, 0, uti), timeout)
    except asyncio.TimeoutError:
        log.warning("shortcut copy: %s not delivered within %.0fs", uti, timeout)
        _busy_until[0] = time.monotonic() + DAEMON_BUSY_SECONDS
        return None
    log.info("text of the shortcut copy resolved in %.2fs", time.monotonic() - started)
    return bytes(data).decode("utf-8", "replace") if data else None
last_html: list = [None]   # HTML of the latest iPhone copy, when available
TEXT_UTIS = ("public.utf8-plain-text", "public.plain-text", "public.text", "public.url")
IMAGE_UTIS = ("public.png", "public.jpeg", "public.heic", "public.tiff", "com.compuserve.gif")
HTML_UTI = "public.html"


async def resolve(rsd, pasteboard_name: str, item_index: int, uti: str) -> Optional[bytes]:
    """RESOLVE one promised representation ({command: DATA, data} reply).

    The daemon loads the representations listed *before* the requested one
    too, but not the ones after it. A copy from Gmail listed public.rtf /
    flat-rtfd after the plain text; asking for everything (matchSource) waited
    30 s+ right after the copy, while text alone avoids those types."""
    from pymobiledevice3.remote.xpc_message import XpcInt64Type
    async with PasteboardService(rsd) as service:
        reply = await service.service.send_receive_request({
            "command": "RESOLVE", "pasteboardName": pasteboard_name,
            "itemIndex": XpcInt64Type(item_index), "type": uti})
    data = reply.get("data")
    return data if isinstance(data, (bytes, bytearray)) else None


async def read_pasteboard(rsd, pasteboard_name: str = "general", allow_full_pull: bool = False):
    """Read the iPhone pasteboard, including text that is only promised.

    Copies from Safari/WebKit (and many apps) carry only a small private
    representation inline; the plain text, HTML and RTF are promised, and the
    pymobiledevice3 reader then reports an empty copy (every text copy the
    user made arrived empty). Promised text (or else an image) is
    fetched with a single bounded RESOLVE."""
    if daemon_busy():
        log.info("iPhone pasteboard still busy with an earlier copy; skipping this change")
        return {}, PasteboardContent()
    started = time.monotonic()
    async with PasteboardService(rsd) as service:
        snapshot = await asyncio.wait_for(service.get(pasteboard_name, POLICY_PROMISE_SECONDARY), 10)
    content = PasteboardContent.from_snapshot(snapshot)
    log.info("pasteboard primary read in %.2fs", time.monotonic() - started)
    last_html[0] = None
    if content.image is not None or (content.text or "").strip(STRIP_CHARS):
        return snapshot, content
    if not RESOLVE_ENABLED[0]:
        return snapshot, content
    board = snapshot.get("pasteboard", snapshot)
    types0 = ((board.get("items") or [{}])[0].get("types") or [])
    trigger = PLAIN_COPY_TRIGGER[0]
    if SHORTCUT_UTI in types0:
        # Our shortcut's own copy: the Shortcuts app serves the text promise
        # itself, so it can be read right away (and must not re-trigger).
        # The automation also runs whenever iCtrl's keyboard connects (session
        # start, a copy request), re-copying what was already there; the
        # receiver drops it when it repeats the latest copy.
        text = await _resolve_text(rsd, pasteboard_name, types0, SHORTCUT_RESOLVE_TIMEOUT)
        if not text:
            return snapshot, PasteboardContent()
        content = PasteboardContent(text=text)
        object.__setattr__(content, "shortcut_echo", True)   # frozen dataclass
        return snapshot, content
    if any(u in types0 for u in TEXT_UTIS) and time.monotonic() - _last_trigger[0] < 10:
        return snapshot, PasteboardContent()      # a trigger is already on its way
    if any(u in types0 for u in TEXT_UTIS):
        # Text is only promised. iCtrl relies on the iPhone Shortcut
        # "ictrlcopy" for it; reading the promise directly can block the
        # pasteboard daemon for ~64 s (Gmail), so that is not done.
        if trigger is None:
            return snapshot, PasteboardContent()
        # Fast path: the iPhone Shortcut "ictrlcopy" (automation: when a
        # keyboard connects) asks the source app for text the way a paste
        # does, which is immediate, and copies it back as plain text. A Gmail
        # copy otherwise costs a 64 s wait inside the pasteboard daemon.
        started = time.monotonic()
        _last_trigger[0] = time.monotonic()
        if PLAIN_COPY_PENDING[0] is not None:
            with contextlib.suppress(Exception):
                PLAIN_COPY_PENDING[0]()
        try:
            await trigger()
        except Exception as error:
            log.warning("plain-copy shortcut trigger failed: %r", error)
        else:
            deadline = time.monotonic() + SHORTCUT_WAIT
            while time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                async with PasteboardService(rsd) as service:
                    fresh = await asyncio.wait_for(service.get(pasteboard_name, POLICY_PROMISE_SECONDARY), 10)
                fresh_types = ((fresh.get("pasteboard", fresh).get("items") or [{}])[0].get("types") or [])
                text = PasteboardContent.from_snapshot(fresh).text
                if not (text and text.strip(STRIP_CHARS)) and SHORTCUT_UTI in fresh_types:
                    text = await _resolve_text(rsd, pasteboard_name, fresh_types, SHORTCUT_RESOLVE_TIMEOUT)
                if text and text.strip(STRIP_CHARS):
                    log.info("plain text from the iCtrl shortcut in %.2fs", time.monotonic() - started)
                    return fresh, PasteboardContent(text=text)
            log.warning("the iCtrl shortcut did not answer within %.0fs", SHORTCUT_WAIT)
        if PLAIN_COPY_FAILED[0] is not None:
            with contextlib.suppress(Exception):
                PLAIN_COPY_FAILED[0]()
        return snapshot, PasteboardContent()
    items = board.get("items", []) or []
    for index, item in enumerate(items[:1]):        # the first item is the copy
        types = item.get("types", []) or []
        wanted = next((u for u in TEXT_UTIS if u in types), None) or             next((u for u in IMAGE_UTIS if u in types), None)
        if wanted is None:
            continue
        started = time.monotonic()
        try:
            data = await asyncio.wait_for(resolve(rsd, pasteboard_name, index, wanted), RESOLVE_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("iPhone copy: %s not delivered within %.0fs; skipped", wanted, RESOLVE_TIMEOUT)
            return snapshot, PasteboardContent()
        log.info("resolved %s of the iPhone copy in %.2fs", wanted, time.monotonic() - started)
        if not data:
            continue
        if wanted in TEXT_UTIS:
            content = PasteboardContent(text=bytes(data).decode("utf-8", "replace"))
            # HTML listed before the text has just been loaded too, so asking
            # for it costs nothing; HTML listed after it might be slow: skip.
            if HTML_UTI in types and types.index(HTML_UTI) < types.index(wanted):
                with contextlib.suppress(Exception):
                    html = await asyncio.wait_for(resolve(rsd, pasteboard_name, index, HTML_UTI), 3)
                    if html:
                        last_html[0] = bytes(html).decode("utf-8", "replace")
            return snapshot, content
        return snapshot, PasteboardContent(image=bytes(data), image_uti=wanted)
    return snapshot, content


def _offers(snapshot) -> bool:
    board = snapshot.get("pasteboard", snapshot)
    return any(uti in _TEXT_OR_IMAGE for item in board.get("items", []) or []
               for uti in item.get("types", []) or [])


# PasteboardMonitor._check() calls the module-level reader; use ours.
_pb.read_pasteboard = read_pasteboard
HISTORY_SIZE = 30


class BoundedPasteboardMonitor(PasteboardMonitor):
    def __init__(self, *args, on_stopped: Callable[[str], None] = lambda reason: None,
                 on_pending: Callable[[], None] = lambda: None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_stopped = on_stopped
        self._on_pending = on_pending

    async def _run(self) -> None:
        failures = 0
        while True:
            try:
                async with NotificationProxyService(self._rsd) as notifications:
                    await notifications.notify_register_dispatch(PASTEBOARD_CHANGED_NOTIFICATION)
                    if self._last_change is None:
                        async with PasteboardService(self._rsd) as service:
                            self._last_change = _change_id(
                                await service.get(self._pasteboard_name, POLICY_ALL_PROMISED))
                    else:
                        await self._check()   # a copy made while the channel was down
                    failures = 0
                    async for notification in notifications.receive_notification():
                        if notification.get("Name") == PASTEBOARD_CHANGED_NOTIFICATION:
                            started = time.monotonic()
                            await self._check()
                            log.info("pasteboard change handled in %.2fs", time.monotonic() - started)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                log.warning("pasteboard watch channel ended (%s: %s), %d/%d",
                            type(error).__name__, error, failures, MAX_CONSECUTIVE_FAILURES)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    self._on_stopped(f"{type(error).__name__}: {error}")
                    return
            await asyncio.sleep(self._reconnect_delay)


@dataclass
class ClipItem:
    id: int
    time: float
    source: str                     # "iphone" | "pc"
    text: Optional[str] = None
    png: Optional[bytes] = None
    width: int = 0
    height: int = 0
    thumb: Optional[str] = field(default=None, repr=False)   # data: URL, small JPEG
    digest: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "time": self.time, "source": self.source,
                "kind": "image" if self.png else "text",
                "text": self.text, "width": self.width, "height": self.height,
                "thumb": self.thumb, "chars": len(self.text) if self.text else 0}


def _thumb(png: bytes) -> tuple[str, int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        w, h = image.size
        image = image.convert("RGB")
        image.thumbnail((240, 240))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), w, h


class ClipHistory:
    def __init__(self):
        self.items: list[ClipItem] = []
        self._ids = itertools.count(1)

    def add(self, source: str, text: Optional[str], png: Optional[bytes]) -> Optional[ClipItem]:
        digest = hashlib.sha1((text or "").encode() + (png or b"")).hexdigest()
        existing = next((i for i in self.items if i.digest == digest), None)
        if existing is not None:
            # Same content copied again: move it to the top, keep its id.
            existing.time, existing.source = time.time(), source
            self.items = [existing] + [i for i in self.items if i is not existing]
            return existing
        item = ClipItem(next(self._ids), time.time(), source, text, png, digest=digest)
        if png:
            item.thumb, item.width, item.height = _thumb(png)
        self.items = [item] + [i for i in self.items if i.digest != digest][:HISTORY_SIZE - 1]
        return item

    def get(self, item_id: int) -> Optional[ClipItem]:
        return next((i for i in self.items if i.id == item_id), None)


def content_parts(content: PasteboardContent) -> tuple[Optional[str], Optional[bytes]]:
    """(text, png) of a device pasteboard content; images converted to PNG."""
    from .pcclip import to_png
    png = None
    if content.image is not None:
        try:
            png = to_png(content.image)
        except Exception as error:
            log.warning("could not decode iPhone clipboard image (%s): %r", content.image_uti, error)
    text = content.text if content.text and content.text.strip() else None
    return text, png
