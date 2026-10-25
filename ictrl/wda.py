"""Screen elements (labels and positions) through WebDriverAgent.

iOS reports the accessibility tree with positions only to an XCUITest runner.
iCtrl starts the user's sideloaded WebDriverAgent runner (any app whose
executable is "WebDriverAgentRunner-Runner") as a UI test through
testmanagerd and talks HTTP to it on device port 8100 through the session's
own tunnel (USB and Wi-Fi alike; no usbmux / localhost proxy).

Observed on iOS 27: with the device locked the runner aborts in
XCTRunnerDaemonSession; a runner left from an earlier iCtrl process answers
/status but aborts on the first UI query, so a fresh run replaces it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

from pymobiledevice3.services.dvt.testmanaged.dtx_services import XCUITestListener

log = logging.getLogger(__name__)

PORT = 8100
RUNNER_EXECUTABLE = "WebDriverAgentRunner-Runner"
ICTRL_BUNDLE = "com.ictrl.wda"


def pick_runner(apps: dict) -> Optional[str]:
    """The WebDriverAgent runner to start: any installed one works (another
    tool's too), and with several, iCtrl's own (its bundle ID, which signing
    tools may extend with a suffix, or its display name) comes first."""
    runners = sorted(b for b, info in apps.items() if info.get("CFBundleExecutable") == RUNNER_EXECUTABLE)
    if not runners:
        return None
    own = [b for b in runners if b == ICTRL_BUNDLE or b.startswith(ICTRL_BUNDLE + ".")
           or apps[b].get("CFBundleDisplayName") == "iCtrl WDA"]
    chosen = (own or runners)[0]
    if len(runners) > 1:
        log.info("WebDriverAgent runners installed: %d; using %s", len(runners), chosen)
    return chosen
START_TIMEOUT = 40.0
# Computing "visible"/"accessible" for every node made /source take ~46 s;
# without them it takes ~2 s.
SOURCE_PATH = "/source?format=xml&excluded_attributes=visible,accessible,enabled,index,traits"
MODAL_TYPES = ("XCUIElementTypeAlert", "XCUIElementTypeSheet")
SKIP_TYPES = {"Application", "Window", "Other", "Image", "ScrollView", "Group", "LayoutArea",
              "LayoutItem"}


class WdaError(RuntimeError):
    pass


class _XCTestStartupListener(XCUITestListener):
    """Forward lifecycle events without delaying the DTX callback."""

    def __init__(self, entry):
        self.entry = entry

    def _notify(self, kind: str) -> None:
        log.info("XCTest startup event: %s", kind)
        self.entry.notify_startup_event(kind)

    async def test_bundle_ready(self) -> None:
        self._notify("bundle-ready")

    async def test_bundle_ready_with_protocol_version(self, protocol_version, minimum_version) -> None:
        self._notify("bundle-protocol")

    async def test_bundle_ready_with_capabilities(self, capabilities):
        self._notify("bundle-capabilities")
        return None  # Keep the library's per-run XCTest configuration.

    async def did_begin_initializing_for_ui_testing(self) -> None:
        self._notify("ui-initializing")

    async def did_begin_executing_test_plan(self) -> None:
        self._notify("test-plan-started")


class WdaAgent:
    def __init__(self, session):
        self.session = session
        self._task: Optional[asyncio.Task] = None
        self._start_task: Optional[asyncio.Task] = None
        self._start_waiters: dict[asyncio.Task, int] = {}
        self._ready = False
        self._lock = asyncio.Lock()
        self.runner: Optional[str] = None
        self.sid: Optional[str] = None
        self._last_app: Optional[str] = None   # name of the app in front at the last read
        self._last_stop: Optional[float] = None

    # ------------------------------------------------------------ lifecycle
    async def ensure(self) -> None:
        async with self._lock:
            if self._ready and self._task is not None and not self._task.done():
                return
            if self._start_task is None or self._start_task.done():
                self._start_task = asyncio.create_task(self._start(), name="ictrl-wda-start")
            startup = self._start_task
            self._start_waiters[startup] = self._start_waiters.get(startup, 0) + 1
        abandoned = False
        try:
            # Callers share readiness, not merely a running XCTest task.
            # One cancelled request must not cancel another caller's start.
            await asyncio.shield(startup)
        except asyncio.CancelledError:
            abandoned = True
            raise
        finally:
            async with self._lock:
                remaining = self._start_waiters[startup] - 1
                if remaining:
                    self._start_waiters[startup] = remaining
                else:
                    self._start_waiters.pop(startup, None)
                    if abandoned and not startup.done():
                        startup.cancel()
                        with contextlib.suppress(BaseException):
                            await startup

    async def stop(self) -> None:
        """End the UI-test run and the runner process on the phone: a runner
        outlives its test session (and keeps UI automation enabled) unless
        it is killed."""
        async with self._lock:
            startup, self._start_task = self._start_task, None
            if startup is not None and not startup.done():
                startup.cancel()
                with contextlib.suppress(BaseException):
                    await startup
            await self._stop_run()

    async def close(self) -> None:
        """The session is ending: the runner is always stopped. WDA answers
        HTTP without authentication on every interface of the phone, so it
        never outlives the work that needs it (no resident mode)."""
        await self.stop()

    async def _stop_run(self, kill: bool = True) -> None:
        """Called under the lifecycle lock, or by the sole startup task."""
        task, self._task = self._task, None
        self._ready = False
        self.sid = None
        self._last_stop = time.monotonic()
        if task:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        # Also a runner opened from the home screen: while it runs iOS shows
        # "Automation Running" (an iOS indicator that apps cannot hide).
        if kill:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._kill_old_runner(), 5)
        with contextlib.suppress(Exception):
            self.session._set(automation=False)

    async def _find_runner(self) -> str:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        async with InstallationProxyService(lockdown=self.session.rsd) as proxy:
            apps = await proxy.get_apps(application_type="User")
        bundle = pick_runner(apps)
        if bundle is None:
            raise WdaError("iCtrl WDA（WebDriverAgent）が iPhone に入っていません。"
                           "README の「補助アプリ（WebDriverAgent）を入れる」の手順で入れてください")
        return bundle

    async def _kill_old_runner(self) -> bool:
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        found = False
        async with AppServiceService(self.session.rsd) as svc:
            for proc in await svc.list_processes():
                url = str(proc.get("executableURL", {}).get("relative", "") or "")
                if url.endswith("/" + RUNNER_EXECUTABLE):
                    pid = int(proc["processIdentifier"])
                    found = True
                    log.info("stopping the WebDriverAgent runner (pid %d)", pid)
                    # The reply to SIGKILL is a closed channel (iOS 27); the
                    # process is gone all the same.
                    with contextlib.suppress(Exception):
                        await svc.send_signal_to_process(pid, 9)
                    return found
        return found

    async def _start(self) -> None:
        succeeded = False
        try:
            await self._start_run()
            succeeded = True
        finally:
            if not succeeded:
                await self._stop_run()

    async def _start_run(self) -> None:
        from pymobiledevice3.services.dvt.testmanaged.xcuitest import TestConfig, XCUITestService
        from .passcode import PasscodeError, XCTestAutoEntry
        self.runner = self.runner or await self._find_runner()
        since = None if self._last_stop is None else time.monotonic() - self._last_stop
        log.info("WebDriverAgent start: mode=%s, since last stop=%s", getattr(self.session, "wda_mode", "on_demand"),
                 "first" if since is None else f"{since:.0f}s")
        await self._kill_old_runner()
        cfg = await TestConfig.create_for(self.session.rsd, runner_bundle_id=self.runner)
        # XCTest comes from the developer disk image mounted on the phone
        # (iOS 17+), so the runner needs no Apple frameworks inside it: the
        # unmodified Appium release works and nothing of Xcode is
        # redistributed.
        cfg.runner_app_env = {
            "DYLD_FRAMEWORK_PATH": "/System/Developer/Library/Frameworks:/System/Developer/Library/PrivateFrameworks",
            "DYLD_LIBRARY_PATH": "/System/Developer/usr/lib",
        }
        started = time.perf_counter()
        entry = XCTestAutoEntry(self.session)
        helper = asyncio.create_task(entry.watch(), name="ictrl-wda-passcode")
        self._task = runner = asyncio.create_task(
            XCUITestService(self.session.rsd).run(cfg, listener=_XCTestStartupListener(entry)), name="ictrl-wda")

        def runner_ended(_task):
            # Stop typing immediately on the XCTest task's end event, even
            # while an HTTP readiness request or configuration is pending.
            if not helper.done():
                helper.cancel()

        runner.add_done_callback(runner_ended)
        last: Optional[BaseException] = None
        try:
            # The AX helper shares this startup lifetime, including when
            # a call prevents video from running.
            while time.perf_counter() - started < START_TIMEOUT:
                if runner.done():
                    error = None if runner.cancelled() else runner.exception()
                    raise WdaError(start_failure_message(error))
                if helper.done():
                    helper.result()
                try:
                    status = await self._startup_status(runner, helper)
                except (OSError, ConnectionError, WdaError, asyncio.TimeoutError, EOFError) as error:
                    if runner.done():
                        raise
                    last = error
                else:
                    if (status.get("value") or {}).get("ready"):
                        # Once WDA is ready no new entry may begin. An entry
                        # already being sent finishes its one attempt first.
                        if not helper.done() and not entry.sending:
                            helper.cancel()
                        try:
                            await helper
                        except asyncio.CancelledError:
                            if asyncio.current_task().cancelling():
                                raise
                        if runner.done():
                            error = None if runner.cancelled() else runner.exception()
                            raise WdaError(start_failure_message(error))
                        await self._configure()
                        if runner.done():
                            error = None if runner.cancelled() else runner.exception()
                            raise WdaError(start_failure_message(error))
                        entry.confirmed()
                        self._ready = True
                        self.session._set(automation=True)
                        runner.add_done_callback(self._run_done)
                        log.info("WebDriverAgent ready in %.1fs", time.perf_counter() - started)
                        return
                await asyncio.sleep(0.3)
            raise WdaError(f"iCtrl WDA（WebDriverAgent）が {START_TIMEOUT:.0f} 秒以内に起動しませんでした。"
                           "XCTest のパスコード要求が出ている場合は、iCtrl のパスコード設定と自動入力の状態を確認してください。"
                           f"要求が出ていなければ、Sideloadly か AltStore で iCtrl WDA を入れ直してください: {last!r}")
        except PasscodeError as error:
            raise WdaError(str(error)) from None
        finally:
            runner.remove_done_callback(runner_ended)
            if not helper.done():
                helper.cancel()
            with contextlib.suppress(BaseException):
                await helper

    async def _startup_status(self, runner: asyncio.Task, helper: asyncio.Task) -> dict:
        """One readiness request, interrupted by a launch/helper failure."""
        probe = asyncio.create_task(self.request("GET", "/status", timeout=3), name="ictrl-wda-status")
        waiting = {probe, runner}
        if not helper.done():
            waiting.add(helper)
        try:
            while True:
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if runner.done():
                    error = None if runner.cancelled() else runner.exception()
                    raise WdaError(start_failure_message(error))
                if helper.done():
                    helper.result()
                    waiting.discard(helper)
                if probe.done():
                    return probe.result()
        finally:
            if not probe.done():
                probe.cancel()
            with contextlib.suppress(BaseException):
                await probe

    def _run_done(self, task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()  # retrieve an exited runner's exception
        if self._task is task:
            self._ready = False
            self.session._set(automation=False)

    async def _configure(self) -> None:
        """By default WDA waits for the app to become idle (animations after
        a tap) before reading the screen: ~1 s after every tap, versus 0.2 s
        when idle. iCtrl reads what is on screen now."""
        session = await self.request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
        sid = session.get("sessionId") or (session.get("value") or {}).get("sessionId")
        self.sid = sid
        # A page blocked by a JavaScript dialog answers no accessibility
        # queries: each read waited ~10 s (default) before using what it had.
        settings = {"waitForIdleTimeout": 0, "animationCoolOffTimeout": 0, "accessibilityDeadline": 2,
                    "respectSystemAlerts": True}
        result = await self.request("POST", f"/session/{sid}/appium/settings", {"settings": settings})
        applied = {k: (result.get("value") or {}).get(k) for k in settings}
        log.info("WebDriverAgent settings: %s", applied)

    # ------------------------------------------------------------ HTTP
    async def request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 30) -> dict:
        payload = json.dumps(body).encode() if body is not None else b""
        head = (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n").encode()
        conn = await self.session.rsd.create_service_connection(PORT)

        async def exchange() -> tuple[bytes, bytes]:
            # WDA keeps the connection open after answering ("Connection:
            # close" notwithstanding): read the headers, then Content-Length.
            await conn.sendall(head + payload)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = await conn.recv_any(65536)
                if not chunk:
                    raise WdaError("WebDriverAgent の応答が途中で切れました（終了した可能性があります）")
                buf += chunk
            header, _, body_part = buf.partition(b"\r\n\r\n")
            length = None
            for line in header.split(b"\r\n")[1:]:
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value.strip())
            while length is not None and len(body_part) < length:
                chunk = await conn.recv_any(65536)
                if not chunk:
                    break
                body_part += chunk
            return header, body_part

        try:
            header, data = await asyncio.wait_for(exchange(), timeout)
        finally:
            with contextlib.suppress(Exception):
                await conn.close()
        status = int(header.split(b" ", 2)[1])
        result = json.loads(data.decode("utf-8")) if data else {}
        if status >= 400:
            message = (result.get("value") or {}).get("message") if isinstance(result.get("value"), dict) else None
            raise WdaError(f"WebDriverAgent エラー {status}: {message or data[:200]!r}")
        return result

    async def session_request(self, method: str, path: str, body: Optional[dict] = None,
                              timeout: float = 30) -> dict:
        """A request under /session/<id> (created with the runner)."""
        await self.ensure()
        try:
            return await self.request(method, f"/session/{self.sid}{path}", body, timeout)
        except WdaError as error:
            if "session" not in str(error).lower():
                raise
            await self._configure()         # the session was dropped: make a new one
            return await self.request(method, f"/session/{self.sid}{path}", body, timeout)

    # ------------------------------------------------------------ device state
    async def locked(self) -> bool:
        await self.ensure()
        return bool((await self.request("GET", "/wda/locked")).get("value"))

    async def alert(self) -> Optional[dict]:
        """The alert on screen (app or system, e.g. a permission request)."""
        dialog = await self._web_dialog()
        if dialog is not None:
            return (await self._web_dialog_elements(dialog))["dialog"]
        try:
            text = (await self.session_request("GET", "/alert/text", timeout=40)).get("value")
        except WdaError:
            return None                     # "no such alert"
        buttons = []
        with contextlib.suppress(WdaError):
            buttons = (await self.session_request("GET", "/wda/alert/buttons", timeout=40)).get("value") or []
        return {"text": text, "buttons": buttons}

    async def alert_action(self, action: str, name: Optional[str] = None) -> dict:
        if action not in ("accept", "dismiss"):
            raise WdaError("action は accept / dismiss です")
        dialog = await self._web_dialog()
        if dialog is not None:
            # WDA's own alert commands wait ~20 s on a web dialog: tap the
            # button read by OCR instead (accept = the rightmost button,
            # dismiss = "キャンセル"/"Cancel" or the leftmost).
            buttons = (await self._web_dialog_elements(dialog))["_buttons"]
            if not buttons:
                raise WdaError("ダイアログのボタンを読み取れませんでした")
            if name:
                target = next((b for b in buttons if b["label"] == name), None) or \
                    next((b for b in buttons if name.lower() in b["label"].lower()), None)
                if target is None:
                    raise WdaError(f"ボタン「{name}」が見つかりません: {[b['label'] for b in buttons]}")
            elif action == "accept":
                target = buttons[-1]
            else:
                target = next((b for b in buttons if b["label"].lower() in ("キャンセル", "cancel")), buttons[0])
            await self.session.run({"op": "tap", "x": target["x"], "y": target["y"], "hold": 0.05})
            return {"ok": True, "tapped": target["label"]}
        await self.session_request("POST", f"/alert/{action}", {"name": name} if name else {})
        return {"ok": True}

    async def orientation(self) -> str:
        return str((await self.session_request("GET", "/orientation")).get("value") or "PORTRAIT")

    async def set_orientation(self, value: str) -> dict:
        await self.session_request("POST", "/orientation", {"orientation": value.upper()})
        self._report_orientation(value.upper())
        return {"ok": True}

    def _report_orientation(self, value: str) -> None:
        """Let the window turn its view to match."""
        if value != getattr(self, "_reported_orientation", None):
            self._reported_orientation = value
            notify = getattr(self.session, "on_orientation", None)
            if notify is not None:
                notify(value)

    async def open_url(self, url: str) -> dict:
        await self.session_request("POST", "/url", {"url": url})
        return {"ok": True}

    async def open_url_once(self, url: str, *, guard) -> dict:
        """Use an already prepared runner; never start or replay inside input."""
        runner, sid = self._task, self.sid

        def ready():
            return (guard() and self._ready and runner is not None and not runner.done()
                    and self._task is runner and sid is not None and self.sid == sid)

        if not ready():
            raise WdaError("クリップボード取得を停止しました（WDAの状態が変わりました）")
        locked = await self.request("GET", "/wda/locked")
        if (locked.get("value") is not False) or not ready():
            raise WdaError("iPhoneのロック状態を確認できないため、クリップボード取得を停止しました")
        # /wda/locked is false even on the illuminated lock screen/keypad.
        # Read once directly: elements() can restart WDA, which is forbidden
        # while this operation owns the input queue.
        source = await self.request("GET", SOURCE_PATH, timeout=60)
        try:
            root = ET.fromstring(source["value"])
            width, height = float(root.get("width", "0")), float(root.get("height", "0"))
            import math
            if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
                raise ValueError()
            passcode = _passcode_visible(root, width, height)
        except (KeyError, TypeError, ValueError, ET.ParseError):
            raise WdaError("iPhoneの画面を確認できないため、クリップボード取得を停止しました") from None
        if passcode or not ready():
            raise WdaError("ロック画面のため、クリップボード取得を停止しました")
        await self.request("POST", f"/session/{sid}/url", {"url": url})
        return {"ok": True}

    async def terminate(self, bundle_id: str) -> dict:
        result = await self.session_request("POST", "/wda/apps/terminate", {"bundleId": bundle_id})
        return {"ok": True, "terminated": bool(result.get("value"))}

    async def _app_element(self) -> str:
        found = await self.session_request("POST", "/element", {"using": "class name",
                                                                "value": "XCUIElementTypeApplication"})
        value = found.get("value") or {}
        return value.get("ELEMENT") or value.get("element-6066-11e4-a52e-4f735466cecf")

    async def pinch(self, scale: float, velocity: Optional[float] = None) -> dict:
        """Two-finger pinch on the app (scale < 1 zooms out, > 1 in)."""
        self.session.last_injected_touch = time.monotonic() + 3   # the gesture takes a while
        element = await self._app_element()
        velocity = velocity if velocity is not None else (1.0 if scale > 1 else -1.0)
        await self.session_request("POST", f"/wda/element/{element}/pinch",
                                   {"scale": scale, "velocity": velocity}, timeout=30)
        return {"ok": True}

    async def rotate(self, degrees: float, velocity: float = 1.0) -> dict:
        """Two-finger rotation on the app (clockwise positive)."""
        self.session.last_injected_touch = time.monotonic() + 3   # the gesture takes a while
        import math
        element = await self._app_element()
        radians = math.radians(degrees)
        await self.session_request("POST", f"/wda/element/{element}/rotate",
                                   {"rotation": radians,
                                    "velocity": abs(velocity) if radians >= 0 else -abs(velocity)}, timeout=30)
        return {"ok": True}

    # ------------------------------------------------------------ elements
    async def elements(self) -> dict:
        """Labelled elements on screen with their centres in 0..1 units.
        (Not cached: the stream carries 30 frames/s even on a still screen,
        so "no new frames" cannot tell an unchanged screen.)"""
        return await self._read_elements()

    # ------------------------------------------------------------ web dialogs
    WEB_DIALOG_APP_NAMES = ("Safari",)
    SHALLOW_DEPTH = 12

    async def _web_dialog(self) -> Optional[dict]:
        """Safari's JavaScript dialog (alert / confirm / prompt), if one is up.

        While it is up the page answers no accessibility queries, and every
        full read (and every element query, even inside the dialog) waits
        ~10 s. A tree only 12 levels deep is read in ~0.1 s and still holds
        the dialog's frame ("SFDialogView"); its text and buttons are then
        read from the screen picture with Windows OCR (iOS 27)."""
        # Asking WDA for the active app also waits ~5 s during a web dialog,
        # so the app comes from the previous read (the dialog appears in the
        # app that was already in front).
        if self._last_app is not None and self._last_app not in self.WEB_DIALOG_APP_NAMES:
            return None
        await self.session_request("POST", "/appium/settings", {"settings": {"snapshotMaxDepth": self.SHALLOW_DEPTH}})
        try:
            data = await self.request("GET", SOURCE_PATH, timeout=20)
        finally:
            await self.session_request("POST", "/appium/settings", {"settings": {"snapshotMaxDepth": 50}})
        root = ET.fromstring(data["value"].encode("utf-8"))
        node = next((n for n in root.iter() if n.get("name") == "SFDialogView"), None)
        if node is None:
            return None
        width = float(root.get("width") or 0) or 1.0
        height = float(root.get("height") or 0) or 1.0
        x, y, w, h = _rect(node)
        return {"box": (x / width, y / height, w / width, h / height), "points": [width, height]}

    async def _web_dialog_elements(self, dialog: dict) -> dict:
        from . import ocr
        session = self.session
        png, _, _ = await session.screen_png()
        crop, left, top = await asyncio.to_thread(ocr.crop_png, png, dialog["box"])
        lines = await ocr.recognize(crop)
        from PIL import Image
        import io
        size = Image.open(io.BytesIO(png)).size
        bx, by, bw, bh = dialog["box"]
        items = []
        bottom = max((l["y"] + l["h"] for l in lines), default=0)
        for line in lines:
            # OCR ran on a 2x enlargement of the dialog's box.
            cx = (left + (line["x"] + line["w"] / 2) / 2) / size[0]
            cy = (top + (line["y"] + line["h"] / 2) / 2) / size[1]
            is_button = line["y"] + line["h"] >= bottom - line["h"] * 1.2
            items.append({"type": "Button" if is_button else "StaticText", "label": line["text"],
                          "x": round(cx, 4), "y": round(cy, 4),
                          "w": round(line["w"] / 2 / size[0], 4), "h": round(line["h"] / 2 / size[1], 4)})
        buttons = sorted((e for e in items if e["type"] == "Button"), key=lambda e: e["x"])
        text = " ".join(e["label"] for e in items if e["type"] == "StaticText")
        return {"elements": items, "points": dialog["points"], "modal": "WebDialog", "passcode": False,
                "dialog": {"text": text, "buttons": [b["label"] for b in buttons]}, "_buttons": buttons}

    async def _read_elements(self) -> dict:
        await self.ensure()
        dialog = await self._web_dialog()
        if dialog is not None:
            result = await self._web_dialog_elements(dialog)
            result.pop("_buttons", None)
            log.info("web dialog read by OCR: %d lines", len(result["elements"]))
            return result
        started = time.perf_counter()
        try:
            data = await self.request("GET", SOURCE_PATH, timeout=60)
        except WdaError:
            # The runner may have ended (e.g. the phone locked): start it once more.
            await self.stop()
            await self.ensure()
            data = await self.request("GET", SOURCE_PATH, timeout=60)
        fetched = time.perf_counter()
        root = ET.fromstring(data["value"].encode("utf-8"))
        self._last_app = (root.get("name") or root.get("label") or "").strip() or None
        width = float(root.get("width") or 0) or 1.0
        height = float(root.get("height") or 0) or 1.0
        # WDA reports frames in the app's orientation; touches and the video
        # are in the panel's portrait space.
        landscape = None
        if width > height:
            landscape = (await self.orientation()).upper()
        self._report_orientation(landscape or "PORTRAIT")
        items: list[dict[str, Any]] = []
        seen = set()
        # An alert or sheet covers the rest of the app: only its own elements
        # (and the keyboard) can be used, so the rest is left out.
        modal = next((n for n in reversed(list(root.iter())) if n.tag in MODAL_TYPES), None)
        allowed = set(map(id, modal.iter())) if modal is not None else None
        if allowed is not None:
            for keyboard in root.iter("XCUIElementTypeKeyboard"):
                allowed.update(map(id, keyboard.iter()))
        covered = _covered(root, width, height)
        for node, x, y in _absolute(root):
            if allowed is not None and id(node) not in allowed:
                continue
            if id(node) in covered:
                continue
            kind = node.tag.replace("XCUIElementType", "")
            label = (node.get("label") or node.get("name") or "").strip()
            if not label or kind in SKIP_TYPES or _internal(label):
                continue
            w, h = float(node.get("width") or 0), float(node.get("height") or 0)
            if w <= 0 or h <= 0 or x + w <= 0 or y + h <= 0 or x >= width or y >= height:
                continue
            cx, cy = (max(x, 0) + min(x + w, width)) / 2, (max(y, 0) + min(y + h, height)) / 2
            key = (label, round(cx), round(cy))
            if key in seen:
                continue
            seen.add(key)
            vw = (min(x + w, width) - max(x, 0)) / width
            vh = (min(y + h, height) - max(y, 0)) / height
            px, py = _to_portrait(cx / width, cy / height, landscape)
            item = {"type": kind, "label": label[:120], "x": round(px, 4), "y": round(py, 4),
                    "w": round(vh if landscape else vw, 4), "h": round(vw if landscape else vh, 4)}
            value = node.get("value")
            if value and value != label:
                item["value"] = value[:120]
            items.append(item)
        log.info("WebDriverAgent: %d elements in %.2fs (source %.3fs, %d bytes; parse %.3fs)", len(items),
                 time.perf_counter() - started, fetched - started, len(data["value"]), time.perf_counter() - fetched)
        result = {"elements": items, "points": [width, height],
                  # WDA's /wda/locked is true only while the screen is off; the
                  # lock screen and the passcode keypad show the padlock
                  # ("proud-lock") or a passcode field (seen on iOS 27).
                  "passcode": _passcode_visible(root, width, height)}
        if landscape:
            result["orientation"] = landscape
        if modal is not None:
            result["modal"] = modal.tag.replace("XCUIElementType", "")
        return result

    async def find(self, text: str, index: int = 0, found: Optional[list] = None) -> dict:
        found = found if found is not None else (await self.elements())["elements"]
        return find_in(found, text, index)


def find_in(found: list, text: str, index: int = 0) -> dict:
    """The element labelled text (exact, then case-folded, then partial)."""
    low = text.lower()
    exact = [e for e in found if e["label"] == text]
    folded = [e for e in found if e["label"].lower() == low]
    prefix = [e for e in found if e["label"].lower().startswith(low)]
    loose = [e for e in found if low in e["label"].lower()]
    # A short text ("1", "AC") only matches whole labels: as a prefix it
    # picked "10月15日" when the key "1" was not on screen.
    matches = exact or folded or ([] if len(text) <= 2 else prefix or loose)
    if not matches:
        raise WdaError(f"画面に「{text}」が見つかりません")
    if index >= len(matches):
        raise WdaError(f"「{text}」は {len(matches)} 個しかありません")
    return {**matches[index], "matches": len(matches)}


def _rect(node) -> tuple[float, float, float, float]:
    return (float(node.get("x") or 0), float(node.get("y") or 0),
            float(node.get("width") or 0), float(node.get("height") or 0))


def _inside(inner, outer, slack: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
            and inner[0] + inner[2] <= outer[0] + outer[2] + slack
            and inner[1] + inner[3] <= outer[1] + outer[3] + slack)


def _absolute(root):
    """Yield (node, x, y) in screen points. Content hosted in another
    process (home-screen widgets) reports frames relative to its host view;
    such a subtree is shifted by the host's origin (observed iOS 27)."""
    stack = [(root, 0.0, 0.0, None)]
    while stack:
        node, dx, dy, parent = stack.pop()
        x, y, w, h = _rect(node)
        x, y = x + dx, y + dy
        if parent is not None and w > 0 and h > 0 and parent[2] > 0 and parent[3] > 0:
            if not _inside((x, y, w, h), parent) and _inside((x + parent[0], y + parent[1], w, h), parent):
                dx, dy = dx + parent[0], dy + parent[1]
                x, y = x + parent[0], y + parent[1]
        yield node, x, y
        here = (x, y, w, h) if w > 0 and h > 0 else parent
        for child in reversed(list(node)):
            stack.append((child, dx, dy, here))


_PASSCODE = re.compile(r"パスコード|passcode", re.I)
_IDENTIFIER = re.compile(r"^[a-z][\w-]*(\.[\w-]+){2,}$")


def _internal(label: str) -> bool:
    """SwiftUI/UIKit class names and reverse-DNS accessibility identifiers
    (e.g. com.apple.settings.sidebar.collectionView) are not visible text."""
    if " " in label:
        return False
    return (label.startswith(("_Tt", "_UI", "UI")) and len(label) > 12) or bool(_IDENTIFIER.match(label))


# ---------------------------------------------------------------- IPA builder
WDA_RELEASES = "https://api.github.com/repos/appium/WebDriverAgent/releases/latest"
WDA_ASSET = "WebDriverAgentRunner-Runner.zip"


def build_ipa(out_path: str, bundle_id: str = ICTRL_BUNDLE, zip_path: Optional[str] = None) -> dict:
    """Unsigned IPA of Appium's real-device WebDriverAgent build, ready for
    AltStore/Sideloadly. Nothing from Xcode is added: on iOS 17+ the runner
    uses the XCTest frameworks of the phone's developer disk image (verified
    on iOS 27 with the unmodified release)."""
    import io
    import plistlib
    import urllib.request
    import zipfile
    source = {}
    if zip_path:
        data = open(zip_path, "rb").read()
        source["zip"] = zip_path
    else:
        with urllib.request.urlopen(WDA_RELEASES, timeout=30) as resp:
            release = json.load(resp)
        asset = next(a for a in release["assets"] if a["name"] == WDA_ASSET)
        with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as resp:
            data = resp.read()
        source.update(release=release["tag_name"], url=asset["browser_download_url"])
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            name = info.filename
            if ".dSYM/" in name or name.endswith(".dSYM/"):
                continue
            content = b"" if name.endswith("/") else src.read(name)
            if name.endswith("-Runner.app/Info.plist") or name.endswith(".xctest/Info.plist"):
                # AltStore needs a provisioning profile for every bundle it
                # signs; one App ID for both keeps a free account's quota.
                plist = plistlib.loads(content)
                plist["CFBundleIdentifier"] = bundle_id
                if name.endswith("-Runner.app/Info.plist"):
                    plist["CFBundleDisplayName"] = "iCtrl WDA"
                content = plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)
            entry = zipfile.ZipInfo("Payload/" + name, info.date_time)
            entry.external_attr = info.external_attr
            dst.writestr(entry, content, zipfile.ZIP_DEFLATED)
    return {"ipa": out_path, "bundle_id": bundle_id, **source}


def _to_portrait(x: float, y: float, landscape: Optional[str]) -> tuple[float, float]:
    """App-orientation 0..1 coordinates -> the portrait panel's 0..1."""
    if not landscape:
        return x, y
    # WDA's LANDSCAPE is UIDeviceOrientationLandscapeLeft: the app's top
    # edge lies along the panel's right edge (checked with Calculator keys
    # on iOS 27).
    if "RIGHT" in landscape:
        return y, 1 - x
    return 1 - y, x


def _on_screen(node, width: float, height: float) -> bool:
    """The cover sheet stays in the tree, slid off screen, while unlocked."""
    x, y, w, h = _rect(node)
    return w > 0 and h > 0 and 0 <= x < width and 0 <= y < height


def _passcode_visible(root, width: float, height: float) -> bool:
    return any((n.get("name") == "proud-lock" and _on_screen(n, width, height)) or
               (n.tag == "XCUIElementTypeSecureTextField"
                and _PASSCODE.search((n.get("label") or "") + " " + (n.get("name") or "")))
               for n in root.iter())


COVER_TYPES = {"XCUIElementTypeOther", "XCUIElementTypeSheet", "XCUIElementTypeAlert",
               "XCUIElementTypePopover", "XCUIElementTypeWindow"}


def _covered(root, width: float, height: float) -> set:
    """Elements hidden behind a presented sheet / popover / alert.

    Exact "hittable" for the whole tree cost ~32 s, so this rule is used
    (from Clock's "add alarm" sheet on iOS 27): a presentation is a later
    branch directly under the window; an element in an earlier branch that
    lies entirely inside a large, not full-screen container of that later
    branch is covered (judged by its centre). Containers inside the same branch (e.g. transparent
    toolbars over a sheet) never hide each other."""
    order = []                                   # [node, rect, start, end, branch]

    def walk(node, branch):
        start = len(order)
        order.append([node, _rect(node), start, start, branch])
        for i, child in enumerate(node):
            walk(child, branch if branch is not None else
                 ((start, i) if node.tag == "XCUIElementTypeWindow" else None))
        order[start][3] = len(order) - 1
    walk(root, None)
    screen = width * height
    covers = [(r, s, b) for n, r, s, e, b in order
              if b is not None and n.tag in COVER_TYPES and r[2] * r[3] >= 0.25 * screen
              and not (r[2] >= width - 1 and r[3] >= height - 1)]
    hidden = set()
    for node, r, s, e, b in order:
        if b is None or r[2] <= 0 or r[3] <= 0:
            continue
        for cr, cs, cb in covers:
            cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
            # Covered when its centre is under the presentation (rows peeking
            # out above a sheet are dimmed background, not usable controls).
            if cb != b and cb[0] == b[0] and cb[1] > b[1] and                     cr[0] <= cx <= cr[0] + cr[2] and cr[1] <= cy <= cr[1] + cr[3]:
                hidden.add(id(node))
                break
    return hidden


# Words iOS uses when it refuses to launch an app whose signature or
# provisioning profile is no longer valid (a free-account AltStore signature
# lasts 7 days).
_SIGNING_WORDS = ("profile", "expired", "signature", "code sign", "untrusted", "not trusted",
                  "verify", "verified", "security", "entitlement", "0xe8008", "invalid code")


def start_failure_message(error: BaseException) -> str:
    text = repr(error)
    if any(word in text.lower() for word in _SIGNING_WORDS):
        return ("iCtrl WDA（WebDriverAgent）の署名が切れているか、信頼されていないため起動できません。"
                f"Sideloadly か AltStore で iCtrl WDA を入れ直してください（AltStore なら更新）: {text}")
    return ("iCtrl WDA（WebDriverAgent）が起動直後に終了しました。iPhone のロックを解除してから、"
            f"もう一度試してください（続く場合は Sideloadly か AltStore で入れ直す）: {text}")
