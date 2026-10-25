"""One CoreDevice session: tunnel + live display stream + HID input.

Lifecycle rules (learned the hard way on this project):

* Failure is detected from events the lower layers already emit — the tunnel
  transport watcher finishing, the media receiver ending, an XPC send raising.
  Silence is never treated as failure: a static screen legitimately stops RTP.
* No automatic reconnect, retry or input replay. A lost session reports its
  first cause and waits for the user (or an agent) to connect again.
* Stop before start: a new session may only open after the previous one has
  completely closed (enforced by the manager's lock and the device mutex).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from unittest import mock

from . import pairing as _pairing

_pairing.use_utsushite_identity()

from . import host, keys
from .video import DeviceLag, HevcAssembler, VideoHub

log = logging.getLogger(__name__)

LOCKDOWN_TIMEOUT = 12
EDGE_BOTTOM = 0.965
SETTLE = 0.12           # s the finger rests before lifting a "settle" swipe (no momentum)
INPUT_MODE_KEYS = ("kana", "eisu", "lang1", "lang2")


class SessionError(RuntimeError):
    pass


def _norm(value: float) -> int:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError("座標は0〜1の範囲で指定してください")
    return round(value * 65535)


@dataclass
class SessionInfo:
    udid: str
    transport: str                      # "usb" | "wifi"
    state: str = "idle"                 # idle|connecting|ready|closing|closed|failed
    stage: str = ""
    error: Optional[str] = None
    name: Optional[str] = None
    model: Optional[str] = None
    ios: Optional[str] = None
    screen: Optional[tuple[int, int]] = None
    wifi_mac: Optional[str] = None
    wifi_ready: bool = False
    automation: bool = False            # WebDriverAgent (iOS "Automation Running") is on
    passcode_state: str = "idle"        # local passcode entry status; never secret data
    own_pairing: bool = False           # USB uses utsushite's own pairing (shown as "utsushite")
    # Why there is no screen video, else None: "off" (not started / stopped by
    # the user), "call" (iOS refused it: microphone or camera in use, error
    # 9022), "camera" (stopped while the camera is in use, see _camera_in_use).
    video_paused: Optional[str] = None
    started: float = field(default_factory=time.time)
    ready_at: Optional[float] = None


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


def validate_clipboard_shortcut_mode(value) -> None:
    # utsushitecopy runs on the virtual keyboard's connection only (a URL would
    # switch the phone to the Shortcuts app).
    if value != "keyboard":
        raise ValueError("utsushitecopy の呼び出しはキーボード接続だけに対応しています")


class DeviceSession:
    def __init__(self, udid: str, transport: str, on_change: Callable[[SessionInfo], None]):
        if transport not in ("usb", "wifi"):
            raise ValueError("transport は usb か wifi です")
        self.info = SessionInfo(udid, transport)
        self._on_change = on_change
        self._stack = contextlib.AsyncExitStack()
        self._closing = False
        self._closed = asyncio.Event()
        self.rsd = None
        self.tunnel = None
        self.lockdown = None
        self.uhs = None
        self.indigo = None
        self.keyboard_id: Optional[int] = None
        self._kb_uhs = None
        self._pending_input_mode = None
        # The last input mode asked for (かな / 英数 usage). iOS puts its own
        # back when another field or app takes the keys or the keyboard is
        # registered again, and nothing tells the PC; so after such a moment
        # the next typed key is preceded by this mode again (the
        # Windows IME was "A" and the iPhone typed Japanese).
        self._input_mode = None
        self._input_mode_stale = False
        self.on_clipboard = None       # set by the manager: called with PasteboardContent
        self.on_clipboard_pending = None
        self.on_clipboard_problem = None
        self._own_write_at = 0.0
        self._wda = None
        self.passcode_input_generation = 0
        self.last_injected_touch = 0.0
        self.own_pairing = False
        self.media_session_id = None
        self.clipboard_watch = "off"   # off | on | stopped: <reason>
        self._pasteboard_monitor = None
        self.clipboard_auto_enabled = True
        self.clipboard_shortcut_mode = "keyboard"
        self._plain_copy_task = None
        self._plain_copy_waiters = {}
        self._plain_copy_generation = 0
        self._clipboard_watch_lock = asyncio.Lock()
        self.hub = VideoHub(self.request_keyframe)
        self.assembler = HevcAssembler()
        self._media = None
        self._rtcp_dest = None
        self._local_ssrc = self._remote_ssrc = 0
        self._last_pli = 0.0
        self._input: asyncio.Queue = asyncio.Queue()
        self._input_task: Optional[asyncio.Task] = None
        self._touch_edge = 0
        self._touch_pos: Optional[tuple[int, int]] = None
        self._keys_down: set[int] = set()
        self._pli_tasks: set[asyncio.Task] = set()
        self.keyframe_requests: dict[str, int] = {}
        self._last_unit_at = 0.0
        self._device_lag = DeviceLag()
        # The screen's orientation as iOS reports it; unknown (upright) until
        # it turns while connected: SpringBoard logs only the change.
        self.rotation = 0
        self.on_rotation: Callable[[int], None] = lambda _rotation: None
        # The window's "PC キーボードを接続" switch. Off, utsushite keeps no virtual
        # keyboard connected, so iOS shows its on-screen keyboard and the user
        # types on it by clicking (also from utsushite).
        self.pc_keyboard = True
        self._video_stack: Optional[contextlib.AsyncExitStack] = None   # while the video runs
        self._camera_active = False

    # ------------------------------------------------------------------ state
    def _set(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.info, key, value)
        with contextlib.suppress(Exception):
            self._on_change(self.info)

    def _stage(self, stage: str) -> None:
        log.info("open stage: %s", stage)
        self._set(stage=stage)

    @property
    def ready(self) -> bool:
        return self.info.state == "ready"

    def _link_lost_message(self) -> str:
        """The connection to the phone ended under us (the tunnel closed)."""
        if self.info.transport == "usb":
            return "iPhone との接続が切れました。ケーブルが抜けていないか確かめて、もう一度接続してください"
        return ("iPhone との接続が切れました。iPhone が同じ Wi-Fi にあるか、"
                "画面が点いているか確かめて、もう一度接続してください")

    def lost(self, reason: str, error: BaseException | None = None) -> None:
        """First cause wins. Closes the session without reopening it."""
        if self._closing or self.info.state == "failed":
            return
        detail = f"{reason}" + (f" ({type(error).__name__}: {error})" if error else "")
        log.warning("session lost: %s", detail)
        # The window shows the reason in plain words; the detail is in the log.
        self._set(state="failed", error=reason)
        asyncio.get_running_loop().create_task(self.close(final_state="failed"))

    # ------------------------------------------------------------------- open
    async def open(self) -> None:
        self._set(state="connecting", error=None)
        try:
            await self._open()
        except BaseException as error:
            log.warning("open failed at stage %s", self.info.stage, exc_info=True)
            if isinstance(error, SessionError):
                message = str(error)
            else:
                # Unexpected (a library error): plain words on screen, the
                # exception and the stage in the log.
                message = "iPhone に接続できませんでした。もう一度接続してください（続くときはケーブルを挿し直すか、iPhone を再起動してください）"
            self._set(state="failed", error=message)
            await self.close(final_state="failed")
            raise
        self._set(state="ready", stage="ready", ready_at=time.time())

    async def _open(self) -> None:
        from packaging.version import Version
        from pymobiledevice3.remote import userspace_tunnel as ut
        from pymobiledevice3.remote.core_device.device_info import DeviceInfoService
        from pymobiledevice3.remote.core_device.hid_service import (
            IndigoHIDService, UniversalHIDServiceService)
        from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy

        host.install_usbmux_identity()
        from . import direct_dial
        direct_dial.install()
        if self.info.transport == "wifi":
            _allow_slow_wifi_handshakes()
        self._stage("ownership")
        ownership = host.DeviceOwnership(self.info.udid)
        ownership.acquire()
        self._stack.callback(ownership.release)

        session = self

        async def provider(serial, autopair, fallback=True):
            if session.info.transport == "wifi":
                return await session._connect_remotepairing(), None
            lockdown = await session._connect_lockdown()
            try:
                session._stage("developer_mode")
                if not await lockdown.get_developer_mode_status():
                    raise SessionError("iPhoneのデベロッパモードを有効にしてください（設定 > プライバシーとセキュリティ）")
                await session._provision_wifi(lockdown)
                if Version(lockdown.product_version) < Version("27.0"):
                    await session._mount_ddi(lockdown)
                session._stage("tunnel")
                proxy = await CoreDeviceTunnelProxy.create(lockdown)
            except BaseException:
                with contextlib.suppress(Exception):
                    await lockdown.close()
                raise
            session.lockdown = lockdown
            return proxy, lockdown

        # pymobiledevice3 exposes no provider parameter; scope the injection
        # to this one call.
        from .tunnel import SessionTunnel
        tunnel = SessionTunnel(
            serial=self.info.udid, autopair=False,
            on_transport_closed=lambda: self.lost(self._link_lost_message()))
        with mock.patch.object(ut, "_create_no_root_tunnel_provider", provider):
            self.rsd = await tunnel.aopen()
        self.tunnel = tunnel
        self._stack.push_async_callback(tunnel.aclose)
        watcher = getattr(tunnel, "_transport_watcher", None)
        if watcher is None:
            raise SessionError("utsushite の部品の版が合わないため接続できません。utsushite を更新してください（プラグインは「claude plugin update utsushite@utsushite」、アプリとして入れた場合は setup.ps1 をもう一度実行）")
        watcher.add_done_callback(
            lambda _t: self.lost(self._link_lost_message()))
        self._enable_nodelay(tunnel.tun)

        if self.info.ios is None:
            props = (self.rsd.peer_info or {}).get("Properties", {})
            # The handshake has no name; the lockdown pymobiledevice3 opened
            # through the tunnel already read the device's values, it among them.
            reported = self.rsd.all_values.get("DeviceName")
            log.info("device name over Wi-Fi: %s", "reported by the phone" if reported
                     else "remembered" if self.info.name else "unknown")
            self._set(ios=self.rsd.product_version, model=props.get("ProductType"), wifi_ready=True,
                      name=reported or self.info.name)
        if Version(self.rsd.product_version) >= Version("27.0") and not self._ddi_services_present():
            await self._mount_ddi(self.rsd, modern=True)

        self._stage("display_info")
        async with DeviceInfoService(self.rsd) as info:
            displays = (await asyncio.wait_for(info.get_display_info(), 10)).get("displays") or []
        primary = next((d for d in displays if d.get("primary")), displays[0] if displays else None)
        size = (primary or {}).get("currentMode", {}).get("size")
        # The orientation now ("rot270" while an app shows LandscapeRight);
        # later turns arrive from SpringBoard's log.
        self.rotation = self.DISPLAY_ROTATIONS.get((primary or {}).get("currentOrientation"), 0)
        if isinstance(size, list) and len(size) == 2:
            self._set(screen=(int(size[0]), int(size[1])))

        if not getattr(self, "video_on_connect", False):
            # Screen video off at connect (setting video_on_connect): AI work
            # does not need it. Control, elements and screenshots still work;
            # the video starts on request (start_video).
            self._set(video_paused="off")
            log.info("connected without screen video (video_on_connect off)")
        elif not await self._start_video_or_pause():
            log.info("connected without screen video: a phone or VoIP call is in progress")

        self._stage("hid")
        self.uhs = await self._stack.enter_async_context(UniversalHIDServiceService(self.rsd))
        surfaces = (await asyncio.wait_for(self.uhs.list_connected_services(), 5)).get("connectedServices", [])
        if not any(s.get("_ServiceID") == 257 for s in surfaces):
            raise SessionError("iPhone の画面を操作する準備ができませんでした。もう一度接続してください")
        self.indigo = await self._stack.enter_async_context(IndigoHIDService(self.rsd))
        self._input_task = asyncio.create_task(self._input_worker(), name="utsushite-input")
        self._stack.push_async_callback(self._stop_input)
        await self._keep_awake()
        # One keyboard for the whole session: connecting it is what runs the
        # "utsushitecopy" automation, so it must not come and go with typing. A
        # finger on the real screen only toggles the on-screen keyboard back.
        if self.clipboard_auto_enabled:
            with contextlib.suppress(Exception):
                await self._ensure_keyboard()
        from .physical import PhysicalTouchWatcher
        watcher = PhysicalTouchWatcher(self, self._physical_touch, self._interface_orientation,
                                       self._camera_in_use)
        if await watcher.start():
            self._stack.push_async_callback(watcher.stop)
        await self._watch_clipboard()
        # A WebDriverAgent runner left by a connection that dropped would keep
        # UI automation on: end it (WDA itself starts only when an agent asks
        # for screen elements, and stops with this session).
        leftover = asyncio.create_task(self._end_leftover_wda(), name="utsushite-wda-leftover")
        self._stack.push_async_callback(lambda: _cancel(leftover))

    async def _watch_clipboard(self) -> None:
        """Report iPhone copies as they happen (event-driven; see clipboard.py)."""
        if not self.clipboard_auto_enabled:
            return
        from .clipboard import BoundedPasteboardMonitor

        def changed(content):
            if self.clipboard_auto_enabled and self.on_clipboard is not None:
                self.on_clipboard(content)

        def stopped(reason: str):
            self.clipboard_watch = "stopped: " + reason
            self._set()

        def pending():
            # A copy is being read; skip the notice for our own writes.
            if time.monotonic() - self._own_write_at > 2.0 and self.on_clipboard_pending:
                self.on_clipboard_pending()

        try:
            monitor = BoundedPasteboardMonitor(self.rsd, changed, on_stopped=stopped, on_pending=pending)
            await monitor.start()
        except Exception as error:   # comfort feature; control works without it
            log.warning("clipboard watch unavailable: %r", error)
            self.clipboard_watch = f"stopped: {type(error).__name__}"
            return
        self._pasteboard_monitor = monitor
        self._stack.push_async_callback(monitor.stop)
        self.clipboard_watch = "on"
        from . import clipboard as cb
        async def automatic_plain_copy():
            if self.clipboard_auto_enabled:
                await self.trigger_plain_copy(automatic=True)

        cb.PLAIN_COPY_TRIGGER[0] = automatic_plain_copy
        cb.PLAIN_COPY_FAILED[0] = lambda: self.on_clipboard_problem and self.on_clipboard_problem("shortcut")
        cb.PLAIN_COPY_PENDING[0] = lambda: self.on_clipboard_pending and self.on_clipboard_pending()
        self._stack.callback(lambda: cb.PLAIN_COPY_PENDING.__setitem__(0, None))
        self._stack.callback(lambda: cb.PLAIN_COPY_TRIGGER.__setitem__(0, None))
        self._stack.callback(lambda: cb.PLAIN_COPY_FAILED.__setitem__(0, None))

    async def set_clipboard_watch(self, enabled: bool) -> None:
        """Apply an explicit setting; join the old reader before changing it."""
        from . import clipboard as cb
        async with self._clipboard_watch_lock:
            self.clipboard_auto_enabled = enabled
            if not enabled:
                cb.PLAIN_COPY_TRIGGER[0] = None
                cb.PLAIN_COPY_PENDING[0] = None
                cb.PLAIN_COPY_FAILED[0] = None
                monitor, self._pasteboard_monitor = self._pasteboard_monitor, None
                if monitor is not None:
                    await monitor.stop()
                if self._closing:
                    await self._cancel_plain_copy()
                self.clipboard_watch = "off"
                self._set()
            elif self.ready and not self._closing and self._pasteboard_monitor is None:
                await self._watch_clipboard()
                self._set()

    async def _keep_awake(self) -> None:
        """Keep the phone from auto-locking while it is being controlled.

        Over Wi-Fi, once the phone had locked after the cable was removed,
        services inside the tunnel stopped answering. One
        assertion is held for the session's lifetime on its own connection;
        closing the session closes that connection. No renewal loop."""
        from pymobiledevice3.services.power_assertion import PowerAssertionService
        try:
            service = await self._stack.enter_async_context(PowerAssertionService(self.rsd))
            await asyncio.wait_for(service.service.send_recv_plist({
                "CommandKey": "CommandCreateAssertion",
                "AssertionTypeKey": "PreventUserIdleDisplaySleep",
                "AssertionNameKey": "utsushite",
                "AssertionTimeoutKey": 3600,  # bound in case the agent outlives our connection
                "AssertionDetailKey": "utsushite is mirroring and controlling this iPhone",
            }), 5)
        except Exception as error:  # comfort feature; control works without it
            log.warning("keep-awake assertion unavailable: %r", error)

    async def _connect_lockdown(self):
        from pymobiledevice3 import usbmux
        from pymobiledevice3.lockdown import create_using_usbmux
        self._stage("usbmux")
        devices = await asyncio.wait_for(usbmux.list_devices(), 5)
        if not any(d.serial == self.info.udid and d.connection_type == "USB" for d in devices):
            raise SessionError("USB でつながった iPhone が見つかりません。ケーブルと、Apple Devices アプリが入っているかを確かめてください")
        self._stage("lockdown")
        from . import pairing
        own = pairing.load(self.info.udid)
        try:
            lockdown = None
            if own is not None:
                # utsushite's own pairing: the phone shows the computer as "utsushite"
                # (the shared usbmuxd record is shown under AltStore's name).
                lockdown = await asyncio.wait_for(
                    create_using_usbmux(serial=self.info.udid, connection_type="USB", autopair=False,
                                        pair_record=own, pairing_records_cache_folder=pairing.PAIRING_DIR),
                    LOCKDOWN_TIMEOUT)
                if not lockdown.paired:
                    log.warning("utsushite's own pairing was not accepted (trust removed on the phone?); using the shared one")
                    await lockdown.close()
                    lockdown = None
            if lockdown is None:
                lockdown = await asyncio.wait_for(
                    create_using_usbmux(serial=self.info.udid, connection_type="USB", autopair=False),
                    LOCKDOWN_TIMEOUT)
        except asyncio.TimeoutError as error:
            raise SessionError("iPhone から応答がありません。ケーブルを挿し直して、もう一度接続してください") from error
        if not lockdown.paired:
            # Neither utsushite's own pairing nor one made by Apple Devices: pair
            # as "utsushite" now (the iPhone asks to trust this computer once).
            await lockdown.close()
            self._stage("pairing")
            try:
                await pairing.create(self.info.udid)
            except Exception as error:
                raise SessionError("iPhone で「このコンピュータを信頼」を許可してください"
                                   f"（{type(error).__name__}）") from error
            own = pairing.load(self.info.udid)
            lockdown = await asyncio.wait_for(
                create_using_usbmux(serial=self.info.udid, connection_type="USB", autopair=False,
                                    pair_record=own, pairing_records_cache_folder=pairing.PAIRING_DIR),
                LOCKDOWN_TIMEOUT)
        self.own_pairing = own is not None and lockdown.paired and lockdown.pair_record is own
        log.info("lockdown pairing: %s", "utsushite's own" if self.own_pairing else "shared (usbmuxd)")
        self._set(own_pairing=self.own_pairing)
        if not lockdown.paired:
            await lockdown.close()
            raise SessionError("この PC はまだ iPhone に信頼されていません。USB でつなぎ、iPhone で「信頼」を押してください")
        self._set(name=lockdown.all_values.get("DeviceName"),
                  model=lockdown.all_values.get("ProductType"),
                  ios=lockdown.product_version,
                  wifi_mac=(lockdown.all_values.get("WiFiAddress") or "").lower() or None)
        return lockdown

    async def _provision_wifi(self, lockdown) -> None:
        """Create the RemotePairing record Wi-Fi control needs, once, over USB.

        On iOS 27 a CoreDevice tunnel requested through Wi-Fi lockdown is
        closed by the phone, and the pairing service also refuses Wi-Fi lockdown. Over the
        trusted USB lockdown the pairing is promptless. Failure here only
        means Wi-Fi is not prepared; it does not affect the USB session."""
        from pymobiledevice3.exceptions import RemotePairingCompletedError
        from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService
        if wifi_ready(self.info.udid):
            self._set(wifi_ready=True)
            return
        self._stage("wifi_pairing")
        service = None
        try:
            service = await RemotePairingLockdownService.create(lockdown)
            await asyncio.wait_for(service.connect(autopair=True), 15)
            log.info("RemotePairing record created for Wi-Fi control")
        except RemotePairingCompletedError:
            # remotepairingdeviced closes the connection once the pairing is
            # made; pymobiledevice3 reports that with this exception.
            log.info("RemotePairing record created for Wi-Fi control")
        except Exception as error:
            log.warning("could not prepare Wi-Fi pairing: %r", error)
        finally:
            if service is not None:
                with contextlib.suppress(Exception):
                    await service.close()
        self._set(wifi_ready=wifi_ready(self.info.udid))

    async def _connect_remotepairing(self):
        """Encrypted RemotePairing channel to the phone on the local network."""
        from pymobiledevice3.remote.tunnel_service import create_core_device_tunnel_service_using_remotepairing
        from . import discovery
        if not wifi_ready(self.info.udid):
            raise SessionError("Wi-Fi 操作の準備ができていません。一度 USB で接続すると自動で準備されます")
        # Only the local network: the phone is found with Bonjour. Connecting
        # from outside (over a VPN such as Tailscale) is out of scope.
        if not discovery.available():
            raise SessionError("Wi-Fi で iPhone を探すには Apple Devices アプリ（または iTunes）が必要です")
        else:
            self._stage("bonjour")
            endpoints = await discovery.find_remotepairing()
        if not endpoints:
            raise SessionError("Wi-Fi で iPhone が見つかりません。同じネットワークにあるか、iPhone の画面が点いているか確かめてください")
        self._stage("remote_pairing")
        failures = []
        for ep in endpoints:
            for address in ep["addresses"]:
                try:
                    service = await asyncio.wait_for(create_core_device_tunnel_service_using_remotepairing(
                        self.info.udid, address, ep["port"], autopair=False), 15)
                except Exception as error:
                    failures.append(type(error).__name__)
                    continue
                self._stage("tunnel")
                return service
        log.warning("Wi-Fi connect failed: %s", ", ".join(failures))
        raise SessionError("Wi-Fi で iPhone に接続できませんでした。iPhone の画面を点けて、もう一度接続してください")

    # Services the developer disk image provides. When the RSD already lists
    # them, the image is mounted and asking the image mounter is skipped.
    # Over Wi-Fi that query took 9-26 s or never returned, and
    # the HID port then refused connections (RST); USB answered in ~0.1 s.
    DDI_SERVICES = ("com.apple.coredevice.displayservice",
                    "com.apple.coredevice.hid.universalhidservice",
                    "com.apple.coredevice.hid.indigo")

    def _ddi_services_present(self) -> bool:
        services = (self.rsd.peer_info or {}).get("Services", {})
        present = all(name in services for name in self.DDI_SERVICES)
        log.info("developer image services %s in RSD", "present" if present else "missing")
        return present

    async def _mount_ddi(self, device, modern: bool = False) -> None:
        from pymobiledevice3.services.mobile_image_mounter import (
            PersonalizedImageMounter, auto_mount)
        self._stage("developer_image")
        started = time.monotonic()
        try:
            await self._mount_ddi_inner(device, modern)
        finally:
            log.info("developer image check took %.2fs", time.monotonic() - started)

    async def _mount_ddi_inner(self, device, modern: bool) -> None:
        from pymobiledevice3.services.mobile_image_mounter import (
            PersonalizedImageMounter, auto_mount)
        if modern:
            from pymobiledevice3.services.cryptexd import CryptexdService
            async with PersonalizedImageMounter(device) as mounter:
                if await mounter.is_image_mounted("Personalized"):
                    return
            await CryptexdService(device).auto_install_ddi()
            return
        async with PersonalizedImageMounter(device) as mounter:
            if await mounter.is_image_mounted("Personalized"):
                return
        await auto_mount(device)

    @staticmethod
    def _enable_nodelay(tun) -> None:
        """The userspace TCP stack defaults to Nagle; small HID reports would
        otherwise wait for an ACK and arrive together with the gesture end."""
        from pmd_pytcp.socket import IPPROTO_TCP, TCP_NODELAY
        original = tun.connect_tcp

        async def connect(addr, port):
            sock = await original(addr, port)
            with contextlib.suppress(Exception):
                sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)
            return sock

        tun.connect_tcp = connect

    # ------------------------------------------------------------------ video
    async def _start_video_or_pause(self) -> bool:
        """Start the screen video. During a phone or VoIP call iOS refuses it
        (CoreDevice error 9022); then the session opens
        without video and control, elements and clipboard still work. The
        video is started again on request (start_video), not by retrying."""
        try:
            await self._start_video()
        except Exception as error:
            if "9022" not in str(error) and "call is currently in progress" not in str(error):
                raise
            self._set(video_paused="call")
            return False
        self._set(video_paused=None)
        return True

    async def screen_png(self, max_side: int = 0) -> tuple[bytes, int, int]:
        """The current screen as PNG, from the Instruments screenshot service
        (the video is decoded only in the browser; the lockdown screenshotr
        service is not offered over the RSD tunnel)."""
        return await self.passcode_screen_png(max_side)

    async def passcode_screen_png(self, max_side: int = 0) -> tuple[bytes, int, int]:
        """Request a new Instruments capture, never a retained video frame."""
        import io
        from PIL import Image
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
        async with DvtProvider(self.rsd) as dvt, Screenshot(dvt) as shot:
            png = await asyncio.wait_for(shot.get_screenshot(), 20)
        image = Image.open(io.BytesIO(png))
        if max_side and max(image.size) > max_side:
            scale = max_side / max(image.size)
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
            out = io.BytesIO()
            image.save(out, "PNG")
            png = out.getvalue()
        return png, image.width, image.height

    async def start_video(self) -> dict:
        """Start the video of a session that opened without it (after a call)."""
        if self.info.video_paused is None:
            return {"ok": True, "video": "already on"}
        if not await self._start_video_or_pause():
            raise SessionError("iPhone のマイクかカメラが使用中（通話・カメラ・録音など）のため、iOS が画面の映像を出せません")
        log.info("screen video started on request")
        return {"ok": True}

    async def stop_video(self, reason: str = "off") -> dict:
        """Stop the screen video, keeping the connection (control, elements,
        screenshots go on): the user's "映像を止める", or the camera (reason)."""
        stack, self._video_stack = self._video_stack, None
        if stack is None:
            return {"ok": True, "video": "already off"}
        await stack.aclose()
        self.hub.reset()
        self._set(video_paused=reason)
        log.info("screen video stopped (%s)", "on request" if reason == "off" else reason)
        return {"ok": True}

    def _camera_in_use(self, in_use: bool) -> None:
        """SpringBoard's recording indicator changed (physical.py).

        iOS does not let the screen video and the camera run together: while
        the video streams, the Camera app's capture session is interrupted as
        if it were in the background (VideoDeviceNotAvailableInBackground) and
        stays black; while the camera runs, starting the video fails with
        CoreDevice error 9022 "The device microphone or camera is currently in
        use" (both observed). So the video gives way to the camera
        and comes back when the camera is no longer in use."""
        if in_use == self._camera_active:
            return
        self._camera_active = in_use
        loop = asyncio.get_running_loop()
        if in_use and self._video_stack is not None:
            log.info("camera in use: stopping the screen video for it")
            loop.create_task(self._quietly(self.stop_video("camera")))
        elif not in_use and self.info.video_paused == "camera":
            log.info("camera no longer in use: starting the screen video again")
            loop.create_task(self._quietly(self.start_video()))

    async def _quietly(self, work) -> None:
        try:
            await work
        except Exception as error:
            log.info("screen video change for the camera did not complete: %s", error)

    async def _start_video(self) -> None:
        from pymobiledevice3.remote.core_device.screen_stream import open_media_receiver
        self._stage("display_stream")
        # A new stream each time (also after "映像を止める"): its sequence
        # numbers, parameter sets and clock start afresh. Reusing the old
        # assembler dropped the new stream's first keyframe as a gap and never
        # re-announced the config, so no picture reached the viewer.
        self.assembler = HevcAssembler()
        self._device_lag = DeviceLag()
        local = contextlib.AsyncExitStack()
        try:
            await self._start_video_in(local, open_media_receiver)
        except BaseException:
            await local.aclose()
            raise
        self._video_stack = local
        self._stack.push_async_callback(local.aclose)

    async def _start_video_in(self, stack, open_media_receiver) -> None:
        display = _display_service(self.rsd)
        await stack.enter_async_context(display)
        media, receiver_ip = open_media_receiver(display, (8 << 20, 4 << 20, 1 << 20))
        self._media = media
        stack.callback(media.close)
        sender_ip = self.rsd.service.address[0]
        # Video and audio share one client session id, as Xcode's mirror does.
        self.media_session_id = uuid.uuid4()
        answer = await asyncio.wait_for(display.start_video_stream(
            receiver_ip=receiver_ip, receiver_port=media.port, sender_ip=sender_ip,
            display_id=1, client_session_id=self.media_session_id), 20)
        cfg = answer.get("connection", {}).get("streamConfig", {})
        # Names are from the device's perspective.
        self._local_ssrc = int(cfg.get("RemoteSSRC", 0))
        self._remote_ssrc = int(cfg.get("LocalSSRC", 0))
        source_port = int(cfg.get("SourcePort", 0))
        self._rtcp_dest = (sender_ip, source_port) if source_port else None
        token = cfg.get("RemoteSSRC")

        async def stop_stream():
            # The stop must be the only reply-bearing request on its own,
            # fresh connection; bounded because iOS may never acknowledge.
            if not isinstance(token, int) or not token:
                return
            with contextlib.suppress(Exception):
                async with _display_service(self.rsd) as fresh:
                    await asyncio.wait_for(fresh.stop_media_stream(
                        stop_all=False, identifiers=[token]), 1.5)

        stack.push_async_callback(stop_stream)
        recv = asyncio.create_task(self._receive(media), name="utsushite-video-recv")
        rtcp = asyncio.create_task(self._rtcp_reports(media), name="utsushite-rtcp")
        recv.add_done_callback(lambda t: self._media_task_done(t, "iPhone との接続が切れました（画面の映像が届かなくなりました）"))

        async def stop_tasks():
            for task in (rtcp, recv):
                task.cancel()
            for task in (rtcp, recv):
                with contextlib.suppress(BaseException):
                    await task

        stack.push_async_callback(stop_tasks)

    def _media_task_done(self, task: asyncio.Task, reason: str) -> None:
        if task.cancelled():
            return
        self.lost(reason, task.exception())

    async def _receive(self, media) -> None:
        assembler, hub = self.assembler, self.hub
        while True:
            packet = await media.recv(65535)
            unit = assembler.feed(packet)
            if assembler.config_changed:
                assembler.config_changed = False
                log.info("video config: %s %dx%d", assembler.config.codec,
                         assembler.config.width, assembler.config.height)
                hub.set_config(assembler.config)
                if assembler.config and self.info.screen is None:
                    self._set(screen=(assembler.config.width, assembler.config.height))
            if unit is not None:
                now = time.monotonic()
                if self._last_unit_at and now - self._last_unit_at > 1.0:
                    # Diagnostics only: a silent phone is legitimate (static
                    # screen), nothing is restarted because of it.
                    log.info("video: %.1fs without frames before this one", now - self._last_unit_at)
                self._last_unit_at = now
                self._device_lag.observe(now, unit.timestamp)
                hub.publish(unit)
            elif assembler.last_dropped:
                # An access unit was dropped on this marker: ask for a keyframe.
                assembler.last_dropped = False
                hub.mark_broken()
                self.request_keyframe("rtp-gap")

    async def _rtcp_reports(self, media) -> None:
        """RTCP receiver reports. The device's streamConfig enables an RTCP
        timeout; without reports its encoder stops after ~20-25 s. This is the
        protocol's keepalive, not a health probe: nothing is inferred from it."""
        while True:
            await asyncio.sleep(1.0)
            if self._rtcp_dest is None or not self.assembler.packets:
                continue
            rr = struct.pack("!BBHII BBBB IIII", 0x81, 0xC9, 7, self._local_ssrc, self._remote_ssrc,
                             0, 0, 0, 0, self.assembler.highest_ext_seq & 0xFFFFFFFF, 0, 0, 0)
            sdes = struct.pack("!BBHI BBBB", 0x81, 0xCA, 2, self._local_ssrc, 1, 0, 0, 0)
            await media.sendto(rr + sdes, *self._rtcp_dest)

    def request_keyframe(self, reason: str = "") -> None:
        """RTCP PLI, rate limited so a loss burst cannot become a PLI storm."""
        self.keyframe_requests[reason] = self.keyframe_requests.get(reason, 0) + 1
        now = time.monotonic()
        if now - self._last_pli < 0.5 or self._media is None or self._rtcp_dest is None:
            return
        if not (self._local_ssrc and self._remote_ssrc):
            return
        self._last_pli = now
        pli = struct.pack("!BBHII", 0x81, 0xCE, 2, self._local_ssrc, self._remote_ssrc)

        async def send():
            with contextlib.suppress(OSError):
                await self._media.sendto(pli, *self._rtcp_dest)

        task = asyncio.get_running_loop().create_task(send())
        self._pli_tasks.add(task)
        task.add_done_callback(self._pli_tasks.discard)
        log.debug("keyframe requested: %s", reason)

    # ------------------------------------------------------------------ input
    def submit(self, command: dict) -> asyncio.Future:
        if not self.ready:
            raise SessionError("iPhoneに接続されていません")
        if command.get("op") in {"touch", "tap", "swipe", "button", "keys", "text", "key",
                                  "plain_copy"}:
            self.passcode_input_generation += 1
        future = asyncio.get_running_loop().create_future()
        self._input.put_nowait((command, future, time.monotonic()))
        return future

    async def run(self, command: dict) -> Any:
        return await self.submit(command)

    async def _stop_input(self) -> None:
        task, self._input_task = self._input_task, None
        if task:
            # Lift anything still held so the phone is not left mid-gesture.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._release_all(), 1)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        while not self._input.empty():
            _, future, _ = self._input.get_nowait()
            if not future.done():
                future.set_exception(SessionError("iPhone との接続が終わりました"))

    async def _input_worker(self) -> None:
        """Single consumer: preserves order. Consecutive touch moves are
        coalesced to the newest position (stale positions only add lag)."""
        while True:
            command, future, queued_at = await self._input.get()
            while (command.get("op") == "touch" and command.get("phase") == "move"
                   and not self._input.empty()):
                nxt, nxt_future, queued_at = self._input._queue[0]  # peek
                if nxt.get("op") != "touch" or nxt.get("phase") != "move":
                    break
                self._input.get_nowait()
                if not future.done():
                    future.set_result({"coalesced": True})
                command, future = nxt, nxt_future
            user_input = command.get("op") in {"touch", "tap", "swipe", "button", "keys", "text", "key",
                                               "plain_copy"}
            try:
                if command.get("op") in ("secret_strokes", "plain_copy") and future.cancelled():
                    command.clear()
                    continue
                started = time.monotonic()
                op = command.get("op")
                result = await self._execute(command)
                if op in ("text", "key") and not self.pc_keyboard and self.keyboard_id is not None:
                    # Typed for the text box or an agent while the PC keyboard
                    # is unlinked: give the on-screen keyboard back.
                    await self._show_phone_keyboard("typed while the PC keyboard is unlinked")
                done = time.monotonic()
                if done - queued_at > 0.3:
                    # Diagnostics only: where an input waited (queue or device).
                    log.info("input %s: waited %.0f ms, ran %.0f ms", op,
                             (started - queued_at) * 1000, (done - started) * 1000)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except (ConnectionError, OSError, asyncio.IncompleteReadError, EOFError) as error:
                if not future.done():
                    future.set_exception(SessionError("iPhone に操作を送れませんでした"))
                self.lost("iPhone との接続が切れました（操作を送れなくなりました）", error)
                continue
            except Exception as error:
                if not future.done():
                    future.set_exception(error)
                continue
            finally:
                if user_input:
                    self.passcode_input_generation += 1
            if not future.done():
                future.set_result(result)

    async def _execute(self, command: dict) -> Any:
        op = command.get("op")
        if op == "touch":
            if command["phase"] == "down":
                await self._hide_phone_keyboard()
            return await self._touch(command["phase"], _norm(command["x"]), _norm(command["y"]))
        if op == "tap":
            await self._hide_phone_keyboard()
            x, y = _norm(command["x"]), _norm(command["y"])
            await self._touch("down", x, y)
            await asyncio.sleep(float(command.get("hold", 0.05)))
            await self._touch("up", x, y)
            return {"ok": True}
        if op == "swipe":
            return await self._swipe(command)
        if op == "button":
            self._input_mode_stale = True
            return await self._button(command["name"], command.get("state", "press"))
        if op == "lock":
            return await self._lock_only()
        if op == "keys":
            return await self._send_keys(set(int(u) for u in command.get("usages", [])))
        if op == "text":
            return await self._type_text(str(command["text"]))
        if op == "key":
            return await self._key_combo(command["key"], command.get("modifiers", []))
        if op == "secret_strokes":
            command["_executing"] = True
            # One input-queue operation. Automatic entry rechecks its grant
            # and screen here, not before waiting behind other commands.
            from .passcode import PasscodeError
            prepared = []
            guard = command.get("guard", lambda: True)
            try:
                if not guard():
                    raise PasscodeError("パスコード入力を停止しました")
                if command.get("register_keyboard", True):
                    await self._ensure_keyboard()
                if not guard() or self._keys_down:
                    raise PasscodeError("キーの状態が変わったため、パスコード入力を停止しました")
                prepare = command.get("prepare")
                prepared = await prepare() if prepare else command["strokes"]
                shift = keys.MODIFIERS["shift"]
                for usage, shifted in prepared:
                    if not guard():
                        raise PasscodeError("パスコード入力を停止しました（再送はしません）")
                    await self._stroke(int(usage), [shift] if shifted else [])
                return {"ok": True}
            except (ConnectionError, OSError, asyncio.IncompleteReadError, EOFError):
                # The worker reports the transport loss, without leaking an
                # exception payload from a secret-bearing operation.
                raise OSError("秘密入力の送信に失敗しました") from None
            except Exception:
                raise PasscodeError("パスコード入力を停止しました（再送はしません）") from None
            finally:
                prepared.clear()
                command.clear()
        if op == "pc_keyboard":
            was, self.pc_keyboard = self.pc_keyboard, bool(command.get("on", True))
            if not self.pc_keyboard:
                await self._show_phone_keyboard("PC keyboard unlinked")
            elif not was:
                # Linked again: connect the virtual keyboard now, so iOS puts
                # its on-screen keyboard away (not only at the next touch).
                await self._hide_phone_keyboard()
            return {"ok": True}
        if op == "show_phone_keyboard":
            return await self._show_phone_keyboard("physical touch")
        if op == "plain_copy":
            command["_executing"] = True
            if not command.get("guard", lambda: True)():
                raise SessionError("クリップボード取得を停止しました")
            return await self._plain_copy()
        if op == "release":
            await self._release_all()
            return {"ok": True}
        raise ValueError(f"unknown command {op!r}")

    async def _touch(self, phase: str, x: int, y: int) -> dict:
        from pymobiledevice3.remote.core_device.hid_service import (
            TOUCHSCREEN_STATE_CONTACT, TOUCHSCREEN_STATE_RELEASE)
        # The physical-touch watcher tells utsushite's own touches apart by time.
        self.last_injected_touch = time.monotonic()
        if phase == "down":
            self._input_mode_stale = True
            # A contact starting at the bottom edge is the Home / App Switcher
            # gesture; iOS only recognises it via an edge digitizer event.
            self._touch_edge = 3 if y >= round(EDGE_BOTTOM * 65535) else 0
        if phase in ("down", "move"):
            if self._touch_pos is None and phase == "move":
                return {"ignored": "no contact"}
            starting = phase == "down"
            self._touch_pos = (x, y)
            if self._touch_edge:
                await self._edge(0 if starting else 1, x, y)
            else:
                await self.uhs.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, x, y)
        elif phase == "up":
            if self._touch_pos is None:
                return {"ignored": "no contact"}
            if self._touch_edge:
                await self._edge(2, x, y)
            else:
                await self.uhs.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, x, y)
            self._touch_pos = None
            self._touch_edge = 0
        else:
            raise ValueError("phase は down/move/up です")
        return {"ok": True}

    async def _edge(self, event_type: int, x: int, y: int) -> None:
        from pymobiledevice3.remote.xpc_message import XpcUInt64Type
        await self.indigo.service.send_request({
            "messageType": "IndigoDigitizerEvent",
            "featureIdentifier": "com.apple.coredevice.feature.remote.hid.digitizer",
            "payload": {
                "pointOne": {"x": x / 65535, "y": y / 65535},
                "eventType": XpcUInt64Type(event_type),
                "edge": XpcUInt64Type(self._touch_edge),
                "target": XpcUInt64Type(0),
            },
        })

    async def _swipe(self, command: dict) -> dict:
        x1, y1 = _norm(command["x1"]), _norm(command["y1"])
        x2, y2 = _norm(command["x2"]), _norm(command["y2"])
        duration = max(0.05, min(5.0, float(command.get("duration", 0.3))))
        steps = max(4, int(duration * 60))
        await self._touch("down", x1, y1)
        start = time.perf_counter()
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)
            await self._touch("move", round(x1 + (x2 - x1) * ease), round(y1 + (y2 - y1) * ease))
            delay = start + duration * t - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        if command.get("settle"):
            # A scroll that must stop where it was asked to (scroll_to,
            # bringing an element into view): the finger rests before it
            # lifts, so iOS adds no momentum. Lifted at once, the list kept
            # moving and the element found had scrolled away.
            await self._touch("move", x2, y2)
            await asyncio.sleep(SETTLE)
        await self._touch("up", x2, y2)
        return {"ok": True}

    async def _lock_only(self) -> dict:
        """The dock's lock button: lock the phone, never wake it. The side
        button toggles (it wakes a dark screen), so it is pressed only while
        the phone is unlocked. PasswordProtected is the current lock state,
        asked fresh each time (an iPhone without a passcode is never locked)."""
        locked = await asyncio.wait_for(self.rsd.get_value(key="PasswordProtected"), 5)
        if type(locked) is not bool:
            raise SessionError("iPhone のロック状態を確認できませんでした")
        if locked:
            return {"ok": True, "pressed": False, "already_locked": True}
        await self._button("lock", "press")
        return {"ok": True, "pressed": True, "already_locked": False}

    async def _button(self, name: str, state: str) -> dict:
        from pymobiledevice3.remote.core_device.hid_service import (
            HID_BUTTON_STATE_DOWN, HID_BUTTON_STATE_UP)
        if name == "app-switcher":
            # Double Home press opens the app switcher on every iPhone model.
            await self._button("home", "press")
            await asyncio.sleep(0.12)
            return await self._button("home", "press")
        if name not in keys.BUTTONS:
            raise ValueError(f"不明なボタン: {name}")
        page, code, hold = keys.BUTTONS[name]
        if state in ("press", "down"):
            await self.indigo.send_button(page, code, HID_BUTTON_STATE_DOWN)
        if state == "press":
            await asyncio.sleep(hold)
        if state in ("press", "up"):
            await self.indigo.send_button(page, code, HID_BUTTON_STATE_UP)
        return {"ok": True}

    async def _ensure_keyboard(self) -> None:
        """Register a virtual hardware keyboard on its own HID connection.

        Verified on a real iPhone: the surface needs a
        unique service ID on a dedicated UniversalHID connection; key strokes
        themselves travel as Indigo keyboard events and pass through the iOS
        input method. Registering the default ID on the touch connection made
        the device close that connection."""
        if self.keyboard_id is not None:
            return
        from pymobiledevice3.remote.core_device.hid_service import UniversalHIDServiceService
        if self._kb_uhs is None:
            self._kb_uhs = await self._stack.enter_async_context(UniversalHIDServiceService(self.rsd))
            # Unregister while this connection is still open (LIFO unwind).
            self._stack.push_async_callback(self._remove_keyboard)
        service_id = 0x100000000 | (uuid.uuid4().int & 0x7FFFFFFF)
        self.keyboard_id = service_id
        try:
            self.keyboard_id = await asyncio.wait_for(self._kb_uhs.create_keyboard_service(
                service_id=service_id, product="utsushite Keyboard", manufacturer="utsushite"), 5)
            self._input_mode_stale = True      # a new keyboard starts in iOS's own mode
        except Exception as error:
            # Only the keyboard's own connection failed; touch input is on a
            # different connection, so report without ending the session.
            with contextlib.suppress(Exception):
                await self._remove_keyboard()
            self.keyboard_id = None
            self._kb_uhs = None
            log.warning("keyboard registration failed: %r", error)
            raise SessionError("iPhone にキーボードをつなげませんでした。もう一度試してください") from error

    async def _remove_keyboard(self) -> None:
        """A registered hardware keyboard hides the iPhone's on-screen
        keyboard, so it is removed again when the PC stops typing."""
        from pymobiledevice3.remote.xpc_message import XpcUInt64Type
        service_id, self.keyboard_id = self.keyboard_id, None
        if service_id is None or self._kb_uhs is None:
            return
        await asyncio.wait_for(self._kb_uhs.service.send_request({
            "featureIdentifier": "com.apple.coredevice.feature.remote.universalhidservice",
            "messageType": "Request",
            "payload": {"removeService": {"_0": XpcUInt64Type(service_id)}},
        }), 1)

    async def _key_event(self, usage: int, down: bool) -> None:
        from pymobiledevice3.remote.xpc_message import XpcUInt64Type
        await self.indigo.service.send_request({
            "messageType": "IndigoKeyboardButtonEvent",
            "featureIdentifier": "com.apple.coredevice.feature.remote.hid.keyboard",
            "payload": {"usageCode": XpcUInt64Type(usage), "state": XpcUInt64Type(1 if down else 2)},
        })
        self.last_key_event = time.monotonic()
        if down:
            self._keys_down.add(usage)
        else:
            self._keys_down.discard(usage)

    async def _before_typing(self) -> None:
        """The PC is about to type: apply an input mode (かな/英数) that was
        held back while the iPhone showed its own keyboard."""
        await self._ensure_keyboard()
        pending, self._pending_input_mode = self._pending_input_mode, None
        if pending is None and self._input_mode_stale:
            pending = self._input_mode
        self._input_mode_stale = False
        if pending is not None:
            await self._stroke(pending, [])

    async def _send_keys(self, usages: set[int]) -> dict:
        """Full set of held keys (from the GUI); sends only the changes."""
        old = set(self._keys_down)
        if any(not keys.is_modifier(u) for u in usages - old):
            await self._before_typing()
        for usage in sorted(old - usages):
            await self._key_event(usage, False)
        # Modifiers (0xE0..) first so they apply to the key pressed with them.
        for usage in sorted(usages - old, reverse=True):
            await self._key_event(usage, True)
        return {"ok": True}

    async def _stroke(self, usage: int, mods: list[int]) -> None:
        for m in mods:
            await self._key_event(m, True)
        await self._key_event(usage, True)
        await self._key_event(usage, False)
        for m in reversed(mods):
            await self._key_event(m, False)

    async def _key_combo(self, key: str, modifiers: list[str]) -> dict:
        usage = keys.usage_for_name(key)
        mods = [keys.MODIFIERS[m] for m in modifiers]
        if key in INPUT_MODE_KEYS:
            self._input_mode = usage
        if key in INPUT_MODE_KEYS and self.keyboard_id is None:
            # Following the Windows IME must not reconnect the keyboard (that
            # hides the phone's own keyboard the user is typing on); apply
            # the mode when the PC operates the phone again.
            self._pending_input_mode = usage
            return {"ok": True, "deferred": True}
        if key in INPUT_MODE_KEYS:
            self._pending_input_mode = None
            await self._ensure_keyboard()
            self._input_mode_stale = False
        elif not keys.is_modifier(usage):
            await self._before_typing()
        else:
            await self._ensure_keyboard()
        await self._stroke(usage, mods)
        return {"ok": True}

    async def _type_text(self, text: str) -> dict:
        """Insert text via the pasteboard and Cmd+V.

        Typing characters as hardware key strokes goes through the iPhone's
        input method: with a Japanese keyboard active a typed space was
        swallowed. Pasting inserts exactly the given
        text in any script; the user's previous clipboard text is restored."""
        if not text:
            return {"ok": True}
        await self._before_typing()
        previous = None
        with contextlib.suppress(Exception):
            previous = await self.get_clipboard()
        await self.set_clipboard(text)
        await self._stroke(keys.CHARS["v"][0], [keys.MODIFIERS["cmd"]])
        if previous is not None and previous != text:
            # Give the paste time to read the pasteboard, then put the user's
            # own text back (non-text clipboard content cannot be restored
            # through this service).
            await asyncio.sleep(0.3)
            with contextlib.suppress(Exception):
                await self.set_clipboard(previous)
        return {"ok": True, "method": "paste"}

    # The iPhone's on-screen keyboard follows who is using the phone. iOS
    # hides it when a hardware keyboard connects and shows it when none is
    # connected (both deterministic). Eject only toggles it and cannot be
    # read back, so utsushite never keeps a model of it (one drifted after a
    # restart); it is pressed only right after a connect.
    async def _physical_touch(self) -> None:
        self.passcode_input_generation += 1
        self._input_mode_stale = True
        if self.keyboard_id is None or self._closing:
            return
        if self._keys_down:
            log.info("physical touch while PC keys are held (%d); keyboard kept", len(self._keys_down))
            return
        await self.run({"op": "show_phone_keyboard"})

    # iOS's interface orientation -> degrees the window turns the phone
    # (clockwise). LandscapeRight is the phone turned left (its bottom on the
    # right), so the window turns it left too. Portrait upside down is shown
    # upright: the window turns only by quarter turns.
    ROTATIONS = {"portrait": 0, "portraitUpsideDown": 0, "landscapeRight": -90, "landscapeLeft": 90}
    # The same from CoreDevice's display info (read when connecting).
    DISPLAY_ROTATIONS = {"rot0": 0, "rot180": 0, "rot270": -90, "rot90": 90}

    def _interface_orientation(self, name: str) -> None:
        """SpringBoard logged that the screen turned (physical.py)."""
        rotation = self.ROTATIONS.get(name)
        if rotation is None or rotation == self.rotation:
            return
        self.rotation = rotation
        log.info("screen turned: %s", name)
        self.on_rotation(rotation)

    async def _show_phone_keyboard(self, reason: str = "physical touch") -> dict:
        """Someone is using the iPhone itself: show its own keyboard.

        Disconnecting the virtual keyboard alone shows it only ~1.1 s later
        (iOS waits after a keyboard goes away). Eject shows it at once; it
        is a toggle, but a freshly connected keyboard always starts with the
        on-screen keyboard hidden, so here it always shows it. Disconnecting
        afterwards keeps it shown without depending on that toggle (both
        measured)."""
        if self.keyboard_id is None or self._keys_down:
            return {"ok": True}
        with contextlib.suppress(Exception):
            await self._button("eject", "press")
        started = time.monotonic()
        await self._remove_keyboard()
        log.info("%s: virtual keyboard disconnected (iPhone keyboard shown) in %.0f ms",
                 reason, (time.monotonic() - started) * 1000)
        return {"ok": True}

    async def _hide_phone_keyboard(self) -> None:
        """The PC operates the phone again: connect the virtual keyboard
        before the touch (so a focused field already has it), which hides
        the on-screen keyboard, and apply an input mode held back meanwhile.
        Not while the PC keyboard is unlinked: the on-screen keyboard is
        what the user types with then."""
        if self.keyboard_id is not None or not self.pc_keyboard:
            return
        started = time.monotonic()
        await self._before_typing()
        log.info("PC touch: virtual keyboard connected (iPhone keyboard hidden) in %.0f ms",
                 (time.monotonic() - started) * 1000)

    async def set_clipboard_shortcut_mode(self, mode: str) -> None:
        validate_clipboard_shortcut_mode(mode)
        async with self._clipboard_watch_lock:
            if mode == self.clipboard_shortcut_mode:
                return
            self.clipboard_shortcut_mode = mode
            self._plain_copy_generation += 1
            await self._cancel_plain_copy()

    async def _cancel_plain_copy(self) -> None:
        task = self._plain_copy_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def trigger_plain_copy(self, *, automatic: bool = False) -> dict:
        """One shared request; WDA startup must run outside the input worker."""
        if automatic and not self.clipboard_auto_enabled:
            return {"skipped": "automatic copy disabled", "mode": self.clipboard_shortcut_mode}
        if not self.ready or self._closing:
            raise SessionError("iPhoneに接続されていません")
        task = self._plain_copy_task
        if task is None or task.done():
            task = self._plain_copy_task = asyncio.create_task(
                self._trigger_plain_copy(self.clipboard_shortcut_mode, automatic,
                                         self._plain_copy_generation), name="utsushite-plain-copy")
        self._plain_copy_waiters[task] = self._plain_copy_waiters.get(task, 0) + 1
        abandoned = False
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            abandoned = bool(asyncio.current_task().cancelling())
            if abandoned:
                raise
            raise SessionError("クリップボード取得が中止されました") from None
        finally:
            remaining = self._plain_copy_waiters[task] - 1
            if remaining:
                self._plain_copy_waiters[task] = remaining
            else:
                self._plain_copy_waiters.pop(task, None)
                if abandoned and not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            if task.done() and self._plain_copy_task is task:
                self._plain_copy_task = None

    async def _trigger_plain_copy(self, mode: str, automatic: bool, generation: int) -> dict:
        command = {"op": "plain_copy"}

        def guard():
            from . import passcode
            return (self.ready and not self._closing and not command.get("_cancelled")
                    and generation == self._plain_copy_generation
                    and not passcode._entering
                    and (not automatic or self.clipboard_auto_enabled))

        command["guard"] = guard
        if not guard():
            raise SessionError("クリップボード取得を停止しました")
        future = self.submit(command)
        try:
            result = await asyncio.shield(future)
            return {**result, "mode": mode}
        except asyncio.CancelledError:
            command["_cancelled"] = True
            if not command.get("_executing"):
                future.cancel()
            else:
                # A sent operation cannot be replayed or undone. Join its end.
                with contextlib.suppress(BaseException):
                    await asyncio.shield(future)
            raise

    async def _plain_copy(self) -> dict:
        """Run the iPhone Shortcut "utsushitecopy".

        Its personal automation fires when an external keyboard connects.
        The typing keyboard stays connected for the whole session (so typing
        never fires it); for a copy it is disconnected and connected again
        once, and the new one simply remains the typing keyboard."""
        if self._keys_down:
            return {"skipped": "keys held"}
        with contextlib.suppress(Exception):
            await self._remove_keyboard()
        await self._ensure_keyboard()
        return {"ok": True}

    async def _release_all(self) -> None:
        if self._touch_pos is not None:
            await self._touch("up", *self._touch_pos)
        for usage in sorted(self._keys_down):
            await self._key_event(usage, False)

    async def release_keyboard(self) -> None:
        await self._release_all()
        await self._remove_keyboard()

    # --------------------------------------------------------------- services
    async def get_clipboard(self) -> Optional[str]:
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService
        async with PasteboardService(self.rsd) as pb:
            return await asyncio.wait_for(pb.get_text(), 5)

    async def read_clipboard(self):
        """Full iPhone pasteboard content (text and/or image)."""
        from .clipboard import read_pasteboard
        _, content = await asyncio.wait_for(read_pasteboard(self.rsd), 20)
        return content

    def _mark_own_copy(self, payload: bytes) -> None:
        self._own_write_at = time.monotonic()
        # Our own writes must not come back as "the user copied on the iPhone".
        from pymobiledevice3.remote.core_device.pasteboard_service import _fingerprint
        if self._pasteboard_monitor is not None:
            self._pasteboard_monitor._recent_host_content[_fingerprint(payload)] = time.monotonic()

    async def set_clipboard(self, text: str) -> None:
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService
        self._mark_own_copy(text.encode("utf-8"))
        async with PasteboardService(self.rsd) as pb:
            await asyncio.wait_for(pb.set_text(text), 5)
        self._mark_own_copy(text.encode("utf-8"))

    async def set_clipboard_image(self, png: bytes) -> None:
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService, data_item
        self._mark_own_copy(png)
        async with PasteboardService(self.rsd) as pb:
            await asyncio.wait_for(pb.set([data_item("public.png", png)]), 10)
        self._mark_own_copy(png)

    async def _end_leftover_wda(self) -> None:
        if self._wda is not None:
            return
        from .wda import WdaAgent
        with contextlib.suppress(Exception):
            await WdaAgent(self)._kill_old_runner()

    def wda(self):
        """WebDriverAgent for screen elements; started on first use and
        stopped with the session."""
        if self._wda is None:
            from .wda import WdaAgent
            self._wda = WdaAgent(self)
            self._stack.push_async_callback(self._wda.close)
        return self._wda

    async def list_apps(self) -> list[dict]:
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        async with AppServiceService(self.rsd) as svc:
            apps = await asyncio.wait_for(svc.list_apps(
                include_app_clips=False, include_hidden_apps=False,
                include_internal_apps=False), 20)
        result = []
        for app in apps:
            bundle = app.get("bundleIdentifier")
            if bundle:
                result.append({"bundle_id": bundle, "name": app.get("name") or bundle,
                               "version": app.get("version"),
                               "removable": bool(app.get("isRemovable"))})
        result.sort(key=lambda a: a["name"].lower())
        return result

    async def launch_app(self, bundle_id: str) -> dict:
        """On a locked phone the launch request neither fails nor returns: it
        waits for the unlock (iOS 27.0.1), and the caller
        only saw a timeout. The current lock state is asked first instead."""
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        if await asyncio.wait_for(self.rsd.get_value(key="PasswordProtected"), 5) is True:
            raise SessionError("iPhone がロックされているため、アプリを開けません。ロックを解除してください")
        async with AppServiceService(self.rsd) as svc:
            await asyncio.wait_for(svc.launch_application(bundle_id, kill_existing=False), 15)
        return {"ok": True}

    # ------------------------------------------------------------------ close
    async def close(self, final_state: str = "closed") -> None:
        if self._closing:
            await self._closed.wait()
            return
        self._closing = True
        if self.info.state not in ("failed",):
            self._set(state="closing")
        try:
            # An explicit off operation may already be joining the reader.
            # Join that operation before the stack destroys its transport.
            await self.set_clipboard_watch(False)
        except Exception as error:
            log.warning("clipboard shutdown failed (%s)", type(error).__name__)
        try:
            await asyncio.wait_for(self._stack.aclose(), 15)
        except Exception as error:  # still report; ownership is released by the stack
            log.warning("close error: %s", error)
        finally:
            self.hub.close()
            self._set(state=final_state if self.info.state != "failed" else "failed", stage="")
            self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()


WIFI_FIRST_REPLY_TIMEOUT = 15.0


def _allow_slow_wifi_handshakes() -> None:
    """RemoteXPC waits only 3 s for a service's first reply. Over Wi-Fi the
    phone sometimes answered later (display/deviceinfo
    handshakes timed out at 3 s while the same services answered at once on
    the next attempt), most likely the phone's Wi-Fi power saving. Widen the
    bound and log how long each handshake really took."""
    from pymobiledevice3.remote import remotexpc
    remotexpc.FIRST_REPLY_TIMEOUT = max(remotexpc.FIRST_REPLY_TIMEOUT, WIFI_FIRST_REPLY_TIMEOUT)
    cls = remotexpc.RemoteXPCConnection
    if getattr(cls._do_handshake, "_utsushite_timed", False):
        return
    original = cls._do_handshake

    async def _do_handshake(self, *args, **kwargs):
        started = time.monotonic()
        try:
            return await original(self, *args, **kwargs)
        finally:
            elapsed = time.monotonic() - started
            if elapsed > 1.0:
                log.info("slow RemoteXPC handshake: %.2fs", elapsed)

    _do_handshake._utsushite_timed = True
    cls._do_handshake = _do_handshake


def wifi_ready(udid: str) -> bool:
    from pymobiledevice3.pair_records import iter_remote_paired_identifiers
    with contextlib.suppress(Exception):
        return udid in set(iter_remote_paired_identifiers())
    return False


def _display_service(rsd):
    """DisplayService using odd request IDs: on this RemoteXPC service an even
    request ID makes the device daemon assert when it replies."""
    from pymobiledevice3.remote.core_device.display_service import DisplayService

    class _Display(DisplayService):
        async def connect(self):
            await super().connect()
            original = self.service.send_request

            async def send_request(data, wanting_reply=False):
                self.service.next_message_id[1] |= 1
                await original(data, wanting_reply=wanting_reply)

            self.service.send_request = send_request

    return _Display(rsd)
