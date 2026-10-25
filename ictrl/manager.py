"""Owns at most one DeviceSession and serialises connect/disconnect."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from . import paths  # noqa: F401  (moves ~/.ictrl once, before anything else)
from . import host
from .session import DeviceSession, SessionError, SessionInfo, validate_clipboard_shortcut_mode

log = logging.getLogger(__name__)

USBMUX_PORT = 27015   # Apple Mobile Device Service on Windows


def usbmux_listening() -> bool:
    """Whether Apple's USB service listens (read from the TCP table)."""
    if os.name != "nt":
        return True
    try:
        import psutil
        return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == USBMUX_PORT
                   for c in psutil.net_connections("tcp"))
    except Exception:
        return True   # cannot tell: let the connect report it

# Not under AppData: the Microsoft Store Python silently redirects AppData
# writes into its package cache, where nobody would find the logs.
# WebDriverAgent: started when an agent reads elements and stopped when it
# finishes ("on_demand"), or never used: elements come from OCR ("off").
# There is no resident mode: WDA has no authentication and listens on every
# interface of the phone, so it never stays running between tasks.
WDA_MODES = ("on_demand", "off")
# Nobody is using the phone (no iCtrl window open, no agent working): the
# connection (tunnel, screen stream, clipboard and log watches) is closed so
# nothing keeps reaching the phone in the background.
IDLE_AFTER_WINDOW = 30      # s after the last window closed (a reload comes back sooner)
IDLE_AFTER_AGENT = 300      # s after an agent's last request, if it never said it finished
# Even with an iCtrl screen open: no operation by a person (taps, keys from an
# iCtrl screen) and no agent request for this long closes the connection.
IDLE_NO_USE = 300
# With no page open, no iPhone connection and no agent request, the server
# itself ends (a window or an agent starts it again when needed).
SERVER_IDLE_EXIT = 60
from .paths import CONFIG_DIR as DATA_DIR  # settings and the device list (paths.py)


class Manager:
    def __init__(self):
        self.session: Optional[DeviceSession] = None
        self._lock = asyncio.Lock()
        self._opening: Optional[asyncio.Task] = None
        self._listeners: set[asyncio.Queue] = set()
        # The Windows IME as last read (True = あ), followed by the iPhone.
        self.windows_ime: Optional[bool] = None
        self._ime_lock = asyncio.Lock()
        self._was_ready = False
        self.windows = 0                     # iCtrl windows (pages) connected
        self.agent_running = 0               # agent requests in progress
        self._idle_task: Optional[asyncio.Task] = None
        self._automation_task: Optional[asyncio.Task] = None
        self._use_task: Optional[asyncio.Task] = None
        self.app_windows = 0                 # of those on screen: the app window (iCtrlWindow)
        self.pages = 0                       # iCtrl pages connected, shown or hidden
        self._exit_task: Optional[asyncio.Task] = None
        self.on_unused = None                # set by the server: ends the process
        self._known_path = DATA_DIR / "devices.json"
        self.known: dict[str, dict] = self._load_known()
        from .clipboard import ClipHistory
        self.clips = ClipHistory()
        self._settings_path = DATA_DIR / "settings.json"
        self.settings = {"auto_copy_to_pc": True, "video_on_connect": True, "show_ime_button": False,
                         "auto_disconnect": True,
                         "clipboard_shortcut_mode": "keyboard", **self._load_settings()}
        try:
            validate_clipboard_shortcut_mode(self.settings["clipboard_shortcut_mode"])
        except ValueError:
            self.settings["clipboard_shortcut_mode"] = "keyboard"
        from . import shots
        try:
            shots.custom_dir = shots.validate_dir(self.settings.get("screenshot_dir"))
        except ValueError:
            shots.custom_dir = None
        # Settings of retired features (PC copies sent to the iPhone on their
        # own, audio volume restore) are dropped.
        for retired in ("auto_copy_to_iphone", "volume_restore"):
            self.settings.pop(retired, None)
        if self.settings.get("wda_mode") not in (None, *WDA_MODES):
            # e.g. "resident" from an earlier version: WDA no longer stays running.
            self.settings["wda_mode"] = "on_demand"

    # ------------------------------------------------------------ persistence
    def _load_known(self) -> dict:
        with contextlib.suppress(Exception):
            return json.loads(self._known_path.read_text("utf-8"))
        return {}

    def _remember(self, info: SessionInfo) -> None:
        entry = self.known.setdefault(info.udid, {})
        entry.update({k: v for k, v in {"name": info.name, "model": info.model, "ios": info.ios,
                                        "wifi_mac": info.wifi_mac,
                                        "last_transport": info.transport}.items() if v})
        with contextlib.suppress(Exception):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._known_path.write_text(json.dumps(self.known, ensure_ascii=False, indent=1), "utf-8")

    def _load_settings(self) -> dict:
        with contextlib.suppress(Exception):
            return json.loads(self._settings_path.read_text("utf-8"))
        return {}

    def _save_device_value(self, key: str, udid: str, value) -> None:
        saved = dict(self.settings.get(key, {}))
        if value is not None:
            saved[udid] = value
        else:
            saved.pop(udid, None)
        self.settings[key] = saved
        with contextlib.suppress(Exception):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps(self.settings, indent=1), "utf-8")

    async def update_settings(self, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise ValueError("settings must be an object")
        if "clipboard_shortcut_mode" in changes:
            validate_clipboard_shortcut_mode(changes["clipboard_shortcut_mode"])
        allowed = {"auto_copy_to_pc", "video_on_connect", "show_ime_button", "auto_disconnect"}
        updated = {k: bool(v) for k, v in changes.items() if k in allowed}
        if "wda_mode" in changes:
            if changes["wda_mode"] not in WDA_MODES:
                raise ValueError(f"wda_mode must be one of {', '.join(WDA_MODES)}")
            updated["wda_mode"] = changes["wda_mode"]
        if "clipboard_shortcut_mode" in changes:
            updated["clipboard_shortcut_mode"] = changes["clipboard_shortcut_mode"]
        if "screenshot_dir" in changes:
            from . import shots
            updated["screenshot_dir"] = shots.validate_dir(changes["screenshot_dir"])
        async with self._lock:
            self.settings.update(updated)
            if "screenshot_dir" in updated:
                from . import shots
                shots.custom_dir = updated["screenshot_dir"]
            with contextlib.suppress(Exception):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                self._settings_path.write_text(json.dumps(self.settings, indent=1), "utf-8")
            if self.session is not None:
                self.session.wda_mode = self.settings.get("wda_mode", "on_demand")
                # Stop before changing mode; enable only after its mode is set.
                if "auto_copy_to_pc" in changes and not self.settings["auto_copy_to_pc"]:
                    await self.session.set_clipboard_watch(False)
                if "clipboard_shortcut_mode" in changes:
                    await self.session.set_clipboard_shortcut_mode(self.settings["clipboard_shortcut_mode"])
                if "auto_copy_to_pc" in changes and self.settings["auto_copy_to_pc"]:
                    await self.session.set_clipboard_watch(bool(self.settings.get("auto_copy_to_pc")))
        self.notify({"t": "settings", **self.settings})
        return self.settings

    # ------------------------------------------------------------- clipboard
    def _iphone_copied(self, content) -> None:
        """Called by the session when the user copies something on the iPhone."""
        if not self.settings.get("auto_copy_to_pc"):
            return
        asyncio.get_running_loop().create_task(self._handle_iphone_copy(content))

    async def _handle_iphone_copy(self, content) -> None:
        if not self.settings.get("auto_copy_to_pc"):
            return
        from .clipboard import content_parts, last_html
        text, png = await asyncio.to_thread(content_parts, content)
        if not self.settings.get("auto_copy_to_pc"):
            return
        html = last_html[0] if text else None
        if not text and not png:
            log.info("iPhone copy with no text or image (format not supported)")
            return
        latest = self.clips.items[0] if self.clips.items else None
        if getattr(content, "shortcut_echo", False) and (latest is None or latest.text == text):
            # The iCtrl shortcut re-copied what the iPhone already had (it runs
            # when the virtual keyboard connects): nothing new to deliver. At
            # session start it only seeds the history.
            if latest is None:
                await asyncio.to_thread(self.clips.add, "iphone", text, png)
            log.info("iPhone copy repeated by the shortcut; not announced")
            return
        item = await asyncio.to_thread(self.clips.add, "iphone", text, png)
        copied = False
        if self.settings.get("auto_copy_to_pc"):
            try:
                await asyncio.to_thread(self.copy_item_to_pc, item, html)
                copied = True
            except Exception as error:
                log.warning("could not write PC clipboard: %r", error)
        log.info("iPhone copy: %s, copied to PC: %s", "image" if png else f"text ({len(text)} chars)", copied)
        self.notify({"t": "clipboard", "item": item.to_json(), "copied_to_pc": copied})

    def copy_item_to_pc(self, item, html=None) -> None:
        from . import pcclip
        if item.png:
            pcclip.set_image(item.png, item.text)
        else:
            pcclip.set_text(item.text or "", html)

    # ----------------------------------------------------------------- events
    def listen(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._listeners.add(queue)
        queue.put_nowait(self.status())
        return queue

    def unlisten(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def _changed(self, info: SessionInfo) -> None:
        if info.state == "ready":
            self._remember(info)
        ready = info.state == "ready"
        if ready and not self._was_ready:
            # Just connected: the iPhone starts in its own input mode.
            asyncio.get_running_loop().create_task(self.follow_windows_ime("connect", force=True))
            self._arm_no_use()
        self._was_ready = ready
        if info.state in ("closed", "failed"):
            self.arm_exit()                   # the last use may have been this connection
        self.notify(self.status())

    # ------------------------------------------------------------ Windows IME
    async def windows_ime_changed(self, japanese: bool) -> None:
        """imewatch.py: Windows says the IME state changed (or another window
        with its own state came to the front)."""
        async with self._ime_lock:
            await self._apply_windows_ime(japanese, "windows", force=False)

    async def _apply_windows_ime(self, japanese: bool, reason: str, force: bool) -> None:
        changed = japanese != self.windows_ime
        if not changed and not force:
            return
        self.windows_ime = japanese
        if changed:
            log.info("windows ime: %s (%s)", "あ" if japanese else "A", reason)
            self.notify({"t": "ime", "japanese": japanese})
        s = self.session
        if s is not None and s.ready and s.pc_keyboard:
            with contextlib.suppress(Exception):
                await s.run({"op": "key", "key": "kana" if japanese else "eisu", "modifiers": []})

    async def follow_windows_ime(self, reason: str, force: bool = False) -> None:
        """The iPhone's かな / 英数 follows the Windows IME (the taskbar's あ / A,
        whichever app is in front). Called when something may have changed it
        (imewatch.py: an IME key, a click, another window in front); one read
        each time, no polling."""
        from . import winime
        async with self._ime_lock:
            try:
                state = await asyncio.to_thread(winime.state)
            except Exception as error:
                log.info("windows ime: not readable (%r)", error)
                return
            if not state.get("available"):
                return
            await self._apply_windows_ime(bool(state["japanese"]), reason, force)

    def notify(self, event: dict) -> None:
        status = event
        for queue in list(self._listeners):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(status)

    def status(self) -> dict:
        s = self.session
        if s is None:
            return {"t": "status", "state": "idle", "windows_ime": self.windows_ime,
                    "agent_running": self.agent_running, "pages": self.pages}
        info = asdict(s.info)
        hub = s.hub
        info.update({
            "t": "status",
            "video": {"units": hub.units, "keyframes": hub.keyframes, "gop": len(hub.gop),
                      "bytes": hub.bytes,
                      "dropped": s.assembler.dropped,
                      "viewers": len(hub.subscribers),   # windows receiving video now
                      "codec": hub.config.codec if hub.config else None,
                      "width": hub.config.width if hub.config else None,
                      "height": hub.config.height if hub.config else None},
            "keyboard": s.keyboard_id is not None,
            "rotation": s.rotation,
            "windows_ime": self.windows_ime,
            "agent_running": self.agent_running,
            "pages": self.pages,                 # iCtrl screens open (app window, browser tabs)
            "clipboard_watch": s.clipboard_watch,
            "keyframe_requests": dict(s.keyframe_requests),
        })
        return info

    # ---------------------------------------------------------------- devices
    async def reachable(self, transport: str, udid: Optional[str] = None) -> dict:
        """Whether connect() over `transport` can find the iPhone now, looked
        up the way connect() does (before a switch drops the current link)."""
        udid = udid or (self.session.info.udid if self.session else None)
        if transport == "usb":
            listed = [d for d in await self.devices(bonjour=False) if "usb" in d["transports"]]
            if any(not udid or d["udid"] == udid for d in listed):
                return {"ok": True}
            return {"ok": False, "reason": "USB でつながった iPhone がありません。ケーブルを確かめてください"}
        from . import discovery
        from .session import wifi_ready
        if udid and not wifi_ready(udid):
            return {"ok": False, "reason": "Wi-Fi 操作の準備ができていません。一度 USB で接続すると自動で準備されます"}
        if not discovery.available():
            return {"ok": False, "reason": "Wi-Fi で iPhone を探すには Apple Devices アプリ（または iTunes）が必要です"}
        with contextlib.suppress(Exception):
            if await discovery.find_remotepairing():
                return {"ok": True}
        return {"ok": False, "reason": "Wi-Fi で iPhone が見つかりません。同じネットワークにあるか、iPhone の画面が点いているか確かめてください"}

    async def devices(self, bonjour: bool = True) -> list[dict]:
        from pymobiledevice3 import usbmux
        host.install_usbmux_identity()
        found: dict[str, dict] = {}
        from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError
        # Apple's service may not be running (it may run only while an iPhone
        # is plugged in): then there is no USB phone; Bonjour can still find
        # Wi-Fi ones. Windows takes 2 s to refuse a connect to a closed
        # loopback port, so look for the listener first (about 1 ms).
        listed = []
        if usbmux_listening():
            with contextlib.suppress(ConnectionFailedToUsbmuxdError):
                listed = await asyncio.wait_for(usbmux.list_devices(), 5)
        for d in listed:
            entry = found.setdefault(d.serial, {"udid": d.serial, "transports": []})
            kind = "usb" if d.connection_type == "USB" else "wifi"
            if kind not in entry["transports"]:
                entry["transports"].append(kind)
        # Paired phones advertised by Bonjour count as Wi-Fi capable even
        # when the Apple service does not list them as Network devices.
        macs = {e.get("wifi_mac"): udid for udid, e in self.known.items() if e.get("wifi_mac")}
        if macs and bonjour:
            from . import discovery
            if discovery.available():
                with contextlib.suppress(Exception):
                    for phone in await discovery.find_phones():
                        udid = macs.get(phone["mac"])
                        if udid:
                            entry = found.setdefault(udid, {"udid": udid, "transports": []})
                            if "wifi" not in entry["transports"]:
                                entry["transports"].append("wifi")
        from .session import wifi_ready
        for udid, entry in found.items():
            entry.update({k: v for k, v in self.known.get(udid, {}).items() if k != "wifi_mac"})
            entry["wifi_ready"] = wifi_ready(udid)
        return list(found.values())

    # -------------------------------------------------------------- lifecycle
    async def connect(self, udid: Optional[str] = None, transport: str = "usb") -> dict:
        """USB, or Wi-Fi on the same local network (found with Bonjour)."""
        async with self._lock:
            current = self.session
            if current is not None and current.info.state in ("connecting", "ready") \
                    and current.info.transport == transport \
                    and (udid is None or current.info.udid == udid):
                return self.status()
            if current is not None:
                # Stop before start: the previous tunnel and services must be
                # completely closed before the phone sees a new session.
                await current.close()
            if udid is None:
                devices = await self.devices()
                candidates = [d for d in devices if transport in d["transports"]]
                if not candidates and transport == "wifi":
                    # Not advertised yet or MAC unknown: prefer a phone prepared for Wi-Fi.
                    from .session import wifi_ready
                    candidates = ([{"udid": u} for u in self.known if wifi_ready(u)]
                                  or [{"udid": u} for u in self.known])
                if not candidates:
                    raise SessionError("USBで接続されたiPhoneがありません" if transport == "usb"
                                       else "Wi-Fiで見えるiPhoneがありません")
                udid = candidates[0]["udid"]
            self.session = DeviceSession(udid, transport, self._changed)
            # The name remembered from an earlier connection, until the phone
            # reports its own (a renamed phone shows the new name once it does).
            self.session.info.name = self.known.get(udid, {}).get("name")
            self.session.clipboard_auto_enabled = bool(self.settings.get("auto_copy_to_pc"))
            self.session.clipboard_shortcut_mode = self.settings["clipboard_shortcut_mode"]
            self.session.wda_mode = self.settings.get("wda_mode", "on_demand")
            # Screen video at connect (setting, on by default).
            self.session.video_on_connect = bool(self.settings.get("video_on_connect", True))
            self.session.on_clipboard = self._iphone_copied
            self.session.on_clipboard_pending = lambda: self.notify({"t": "clipboard-pending"})
            self.session.on_clipboard_problem = lambda reason: self.notify({"t": "clipboard-error", "reason": reason})
            self.session.on_orientation = lambda value: self.notify({"t": "orientation", "orientation": value})
            self.session.on_rotation = lambda rotation: self.notify({"t": "orientation", "rotation": rotation})
            self._opening = asyncio.current_task()
            try:
                await self.session.open()
            except asyncio.CancelledError:
                raise SessionError("接続を中止しました") from None
            finally:
                self._opening = None
                self._changed(self.session.info)
            return self.status()

    async def disconnect(self) -> dict:
        if self._opening is not None:
            self._opening.cancel()
        async with self._lock:
            if self.session is not None:
                await self.session.close()
            return self.status()

    # ------------------------------------------------------------- not in use
    def page_opened(self) -> None:
        self.pages += 1
        self._cancel_exit()

    def page_closed(self) -> None:
        self.pages = max(0, self.pages - 1)
        self.arm_exit()

    def app_window_closed(self) -> None:
        """The app window was closed (it cannot reload, so this is not a
        passing gap). With no other page and no agent at work, close the
        connection and end the server now instead of after the idle delays."""
        if self.pages or self.agent_running or self.on_unused is None:
            return
        if not self.settings.get("auto_disconnect", True):
            return
        self._cancel_idle()
        self._cancel_exit()

        async def end_now():
            if self._connected():
                log.info("the iCtrl window closed: closing the iPhone connection")
                await self.disconnect()
            if self.pages or self.agent_running:
                return
            log.info("the iCtrl window closed: ending the server")
            self.on_unused()

        asyncio.get_running_loop().create_task(end_now())

    def _connected(self) -> bool:
        return self.session is not None and self.session.info.state in ("ready", "connecting")

    def _cancel_exit(self) -> None:
        if self._exit_task is not None:
            self._exit_task.cancel()
            self._exit_task = None

    def arm_exit(self) -> None:
        """End the server after SERVER_IDLE_EXIT unless a page, an agent or
        a connection uses it again first (each of those cancels it)."""
        if self.pages or self.agent_running or self._connected() or self.on_unused is None:
            return
        self._cancel_exit()

        async def exit_when_unused():
            await asyncio.sleep(SERVER_IDLE_EXIT)
            self._exit_task = None
            if self.pages or self.agent_running or self._connected():
                return
            log.info("not in use (no window, no connection, no agent): ending the server")
            self.on_unused()

        self._exit_task = asyncio.get_running_loop().create_task(exit_when_unused())

    def window_opened(self, app: bool = False) -> None:
        self.windows += 1
        self.app_windows += bool(app)
        self._cancel_idle()

    def window_closed(self, app: bool = False) -> None:
        self.windows = max(0, self.windows - 1)
        if app:
            self.app_windows = max(0, self.app_windows - 1)
        self._arm_idle(IDLE_AFTER_WINDOW, "the last iCtrl window closed")

    def person_used(self) -> None:
        """A person operated the phone from an iCtrl screen."""
        self._arm_no_use()

    def _arm_no_use(self) -> None:
        """Close the connection after IDLE_NO_USE with no operation by a
        person and no agent request, whether or not a screen is open: an open
        screen alone does not keep the phone connected."""
        if self._use_task is not None:
            self._use_task.cancel()

        async def close_when_unused():
            await asyncio.sleep(IDLE_NO_USE)
            self._use_task = None
            if self.agent_running or not self._connected():
                return                       # an agent at work re-arms this when it ends
            if not self.settings.get("auto_disconnect", True):
                return
            log.info("not in use (no operation by a person or an agent for %d s): "
                     "closing the iPhone connection", IDLE_NO_USE)
            await self.disconnect()
            self.arm_exit()

        self._use_task = asyncio.get_running_loop().create_task(close_when_unused())

    def agent_started(self) -> None:
        self.agent_running += 1
        self._cancel_idle()
        self._cancel_exit()
        self._arm_no_use()
        if self._automation_task is not None:
            self._automation_task.cancel()
            self._automation_task = None

    def agent_ended(self) -> None:
        self.agent_running = max(0, self.agent_running - 1)
        self._arm_idle(IDLE_AFTER_AGENT, "no request from the agent")
        self._arm_no_use()
        self._arm_automation_end()
        self.arm_exit()

    def _arm_automation_end(self) -> None:
        """An agent stopped without finishing (its response interrupted, so no
        Stop hook and no ictrl_finish) leaves WebDriverAgent running, and with
        an iCtrl window open the connection stays. End the automation after
        IDLE_AFTER_AGENT without a request; the connection is left alone."""
        if self.agent_running:
            return
        if self._automation_task is not None:
            self._automation_task.cancel()

        async def end_when_idle():
            await asyncio.sleep(IDLE_AFTER_AGENT)
            self._automation_task = None
            s = self.session
            if self.agent_running or s is None or not s.ready or not s.info.automation:
                return
            log.info("no request from the agent: ending UI automation")
            with contextlib.suppress(Exception):
                await s.wda().stop()

        self._automation_task = asyncio.get_running_loop().create_task(end_when_idle())

    def agent_finished(self) -> None:
        """The agent said it is done (ictrl_finish, Claude Code's Stop hook):
        close the connection now, even if its screen is open in the agent's
        browser. Only the person's own app window on screen keeps it."""
        if not self.settings.get("auto_disconnect", True):
            return
        if self.app_windows or self.agent_running or not self._connected():
            return
        self._cancel_idle()

        async def close_now():
            log.info("not in use (the agent finished): closing the iPhone connection")
            await self.disconnect()
            self.arm_exit()

        self._idle_task = asyncio.get_running_loop().create_task(close_now())

    def _cancel_idle(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

    def _arm_idle(self, delay: float, reason: str) -> None:
        """Close the connection after `delay` unless a window or an agent
        uses the phone again first (each of those cancels it)."""
        if not self.settings.get("auto_disconnect", True):
            return
        if self.windows or self.agent_running or self.session is None or self.session.info.state not in ("ready", "connecting"):
            return
        self._cancel_idle()

        async def close_when_idle():
            await asyncio.sleep(delay)
            self._idle_task = None
            if self.windows or self.agent_running:
                return
            log.info("not in use (%s): closing the iPhone connection", reason)
            await self.disconnect()
            self.arm_exit()

        self._idle_task = asyncio.get_running_loop().create_task(close_when_idle())

    def require(self) -> DeviceSession:
        if self.session is None or not self.session.ready:
            raise SessionError("iPhoneに接続されていません（先に接続してください）")
        return self.session

    async def shutdown(self) -> None:
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.close()
