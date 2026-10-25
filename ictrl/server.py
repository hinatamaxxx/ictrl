"""Local HTTP + WebSocket server: GUI, REST API (for agents) and live video."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import json
import logging
import os
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import __version__
from .manager import Manager
from .session import SessionError
from .video import SnapshotUnavailable, unit_message

log = logging.getLogger(__name__)


class _Get:
    """Stand-in request for calling a GET handler internally."""
    method = "GET"


UI_DIR = Path(__file__).parent / "ui"


def ui_build() -> str:
    """Changes whenever a GUI file changes; open windows reload on a new one."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted(UI_DIR.iterdir()):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()[:12]


async def phone_screenshot(session, request) -> tuple[bytes, int, int]:
    """A screenshot without the video stream (during a call)."""
    import io
    from PIL import Image
    max_side = int(request.query_params.get("max", "0") or 0)
    png, width, height = await session.screen_png(max_side)
    if request.query_params.get("format", "jpeg") == "png":
        return png, width, height
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "JPEG", quality=int(request.query_params.get("quality", "80")))
    return out.getvalue(), width, height


BARS = {"SearchField", "TabBar", "Toolbar"}
BOTTOM_STRIP = 0.95     # a touch starting lower is taken for the home gesture (session EDGE_BOTTOM)


def _obstructed(element: dict, elements: list) -> bool:
    """Whether a tap on the element's centre would not reach it: in the
    bottom strip, or under a floating bar listed separately (iOS 27 keeps
    the search field of Settings floating over the last rows)."""
    x, y = element["x"], element["y"]
    if y >= BOTTOM_STRIP:
        return True
    for bar in elements:
        if bar is element or bar.get("type") not in BARS or element.get("type") in BARS:
            continue
        bx, by, bw, bh = bar["x"], bar["y"], bar.get("w", 0), bar.get("h", 0)
        # Only a strip is a bar: Settings also lists a whole-screen
        # "ツールバー" container, under which every row would count as hidden.
        if bh >= 0.2:
            continue
        if abs(x - bx) <= bw / 2 and abs(y - by) <= bh / 2:
            return True
    return False


def draw_marks(png: bytes, elements: list) -> bytes:
    """Number each element on the screenshot (box + label tag)."""
    import io
    from PIL import Image, ImageDraw, ImageFont
    image = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    size = max(12, image.width // 32)
    try:
        font = ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    for i, e in enumerate(elements, 1):
        w, h = e.get("w", 0.04) * image.width, e.get("h", 0.02) * image.height
        cx, cy = e["x"] * image.width, e["y"] * image.height
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        draw.rectangle(box, outline=(255, 0, 170), width=2)
        tag = str(i)
        tw = draw.textlength(tag, font=font)
        tx, ty = max(0, box[0]), max(0, box[1] - size - 2)
        draw.rectangle((tx, ty, tx + tw + 6, ty + size + 2), fill=(255, 0, 170))
        draw.text((tx + 3, ty), tag, fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _retire_startup_shortcut() -> None:
    """"Start iCtrl with Windows" was removed (the server starts
    when a window or an agent needs it and ends when unused). Remove the
    Startup-folder shortcut an earlier version made, if it is still there."""
    import os
    from pathlib import Path
    link = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / "iCtrl.lnk")
    try:
        if link.exists():
            link.unlink()
            log.info("removed the retired Startup shortcut")
    except OSError as error:
        log.warning("could not remove the retired Startup shortcut: %r", error)


def create_app(port: int) -> Starlette:
    manager = Manager()
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def same_origin(headers) -> bool:
        # Browsers always send Origin on WebSocket and cross-site POSTs. Local
        # agents (MCP, curl) send none. Anything else is another web page
        # trying to drive the phone.
        origin = headers.get("origin")
        return origin is None or origin in allowed_origins

    def error(message: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": message}, status_code=status)

    # Requests from agents (MCP over the pipe, scripts, curl) are shown in
    # the window while they run: everything except the GUI's own requests
    # (a browser) and read-only status queries.
    passive = {"/api/ping", "/api/status", "/api/setup", "/api/devices", "/api/reachable", "/api/ime",
               "/api/settings", "/api/clipboard/history"}
    agent_labels = {"/api/elements": "elements", "/api/tap_element": "tap", "/api/wait_for": "elements",
                    "/api/scroll_to": "swipe", "/api/screenshot_marks": "screenshot", "/api/tap_mark": "tap",
                    "/api/open_url": "launch", "/api/terminate": "launch", "/api/pinch": "touch",
                    "/api/rotate": "touch", "/api/alert": "tap", "/api/orientation": "button", "/api/screenshot": "screenshot", "/api/screenshot/save": "screenshot", "/api/apps": "apps", "/api/launch": "launch",
                    "/api/connect": "connect", "/api/disconnect": "connect"}

    agent_request: contextvars.ContextVar[bool] = contextvars.ContextVar("agent_request", default=False)

    def from_agent(headers) -> bool:
        return "mozilla" not in headers.get("user-agent", "").lower()

    def api(handler):
        async def endpoint(request: Request):
            if request.method != "GET" and not same_origin(request.headers):
                return error("forbidden origin", 403)
            path = request.url.path
            is_agent = from_agent(request.headers)
            agent_request.set(is_agent)
            # An agent ending its work (ictrl_finish, Claude Code's Stop hook at
            # the end of every response) is not an action: it must not show
            # "AI が操作中" for a moment as the chat ends.
            finishing = path == "/api/automation/stop"
            tracked = is_agent and path not in passive and not finishing
            if not is_agent and path not in passive:
                manager.person_used()               # e.g. the screenshot button, a paste
            if tracked:
                manager.agent_started()
                manager.notify({"t": "agent", "phase": "start", "running": manager.agent_running,
                                "op": agent_labels.get(
                    path, "clipboard" if path.startswith("/api/clipboard") else path.rsplit("/", 1)[-1])})
            try:
                return await run_handler(request, handler)
            finally:
                if tracked:
                    manager.agent_ended()
                    manager.notify({"t": "agent", "phase": "end", "running": manager.agent_running})
                if is_agent and finishing:
                    manager.agent_finished()        # ictrl_finish / the Stop hook
        return endpoint

    async def run_handler(request: Request, handler):
        try:
            body = await request.json() if request.method == "POST" and \
                (await request.body()) else {}
            result = await handler(request, body)
        except (SessionError, SnapshotUnavailable) as exc:
            return error(str(exc), 409)
        except PermissionError as exc:
            return error(str(exc), 403)
        except (ValueError, KeyError, TypeError) as exc:
            return error(f"invalid request: {exc}")
        except asyncio.TimeoutError:
            return error("iPhoneからの応答がタイムアウトしました", 504)
        except Exception as exc:  # report, never hide
            log.exception("api failure")
            return error(f"{type(exc).__name__}: {exc}", 500)
        if isinstance(result, Response):
            return result
        return JSONResponse(result if isinstance(result, dict) else {"ok": True, "result": result})

    async def index(_request):
        return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def ping(_request, _body):
        return {"ok": True, "app": "iCtrl", "version": __version__}

    async def setup(_request, _body):
        import sys
        return {"python": sys.executable.replace("pythonw.exe", "python.exe"),
                "root": str(Path(__file__).resolve().parent.parent)}

    async def ime(request, body):
        """The Windows IME (the taskbar's あ / A). POST {"japanese": bool}
        switches it (the dock's A / あ); the iPhone follows Windows."""
        from . import winime
        try:
            if request.method == "POST":
                result = await asyncio.to_thread(winime.set_japanese, bool(body["japanese"]))
                await manager.follow_windows_ime("button")
                return result
            return await asyncio.to_thread(winime.state)
        except KeyError:
            raise
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    async def status(request, _body):
        return manager.status()

    async def devices(request, _body):
        # ?bonjour=0: only what Apple's service lists (fast: the USB check
        # when the user picks USB).
        return {"devices": await manager.devices(bonjour=request.query_params.get("bonjour") != "0")}

    async def reachable(request, _body):
        q = request.query_params
        return await manager.reachable(q.get("transport", "usb"), q.get("udid") or None)

    async def connect(_request, body):
        return await manager.connect(body.get("udid"), body.get("transport", "usb"))

    async def disconnect(_request, _body):
        return await manager.disconnect()

    def to_unit(body, kx="x", ky="y"):
        """Accept normalized (0..1) or pixel coordinates with a reference size."""
        ref_w, ref_h = body.get("width"), body.get("height")
        x, y = float(body[kx]), float(body[ky])
        if ref_w and ref_h:
            return x / float(ref_w), y / float(ref_h)
        session = manager.require()
        if (x > 1 or y > 1) and session.info.screen:
            return x / session.info.screen[0], y / session.info.screen[1]
        return x, y

    def agent(op: str, **fields):
        # Lets the window show what an agent is doing on the phone (not the
        # GUI's own requests, e.g. its text box or app list).
        if agent_request.get():
            manager.notify({"t": "agent", "op": op, **fields})

    async def tap(_request, body):
        x, y = to_unit(body)
        agent("tap", x=x, y=y)
        session = manager.require()
        count = max(1, min(3, int(body.get("count", 1))))
        result = None
        for i in range(count):
            if i:
                await asyncio.sleep(0.06)       # within iOS's double-tap interval
            result = await session.run({"op": "tap", "x": x, "y": y, "hold": float(body.get("hold", 0.05))})
        return result

    async def swipe(_request, body):
        x1, y1 = to_unit(body, "x1", "y1")
        x2, y2 = to_unit(body, "x2", "y2")
        agent("swipe", x1=x1, y1=y1, x2=x2, y2=y2, duration=float(body.get("duration", 0.3)))
        return await manager.require().run({"op": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                                            "duration": float(body.get("duration", 0.3))})

    async def button(_request, body):
        agent("button", name=body["name"])
        return await manager.require().run({"op": "button", "name": body["name"],
                                            "state": body.get("state", "press")})

    async def text(_request, body):
        agent("text", length=len(body["text"]))
        return await manager.require().run({"op": "text", "text": body["text"]})

    async def key(_request, body):
        agent("key", key=body["key"])
        return await manager.require().run({"op": "key", "key": body["key"],
                                            "modifiers": body.get("modifiers", [])})

    async def video_stop(_request, _body):
        """Stop the screen video; the connection stays."""
        return await manager.require().stop_video()

    async def video_start(_request, _body):
        """Start the screen video after a call ended (the session opened without it)."""
        return await manager.require().start_video()

    async def shot(request, _body):
        """The current screen, from iOS's screenshot service (the video is
        decoded only in the browser, so the server never decodes it)."""
        session = manager.require()
        data, w, h = await phone_screenshot(session, request)
        screen = session.info.screen or (w, h)
        return Response(data, media_type="image/png" if request.query_params.get("format") == "png" else "image/jpeg",
                        headers={"X-Width": str(w), "X-Height": str(h), "X-Screen-Width": str(screen[0]),
                                 "X-Screen-Height": str(screen[1]), "Cache-Control": "no-store"})

    async def shot_copy(_request, _body):
        """Current iPhone screen -> PC clipboard (as an image)."""
        from . import pcclip
        session = manager.require()
        data, _, _ = await session.screen_png()
        await asyncio.to_thread(pcclip.set_image, data)
        return {"ok": True}

    async def shot_save(_request, body):
        """Current iPhone screen -> the screenshot folder, and the PC clipboard
        (as Snipping Tool does), in the orientation the window shows
        ({"rotation": 0 | 90 | -90}, from the viewer)."""
        from . import pcclip, shots
        session = manager.require()
        data, _, _ = await session.screen_png()
        rotation = int(body.get("rotation") or 0)
        if rotation not in (0, 90, -90):
            return error("rotation must be 0, 90 or -90")
        data = await asyncio.to_thread(shots.as_viewed, data, rotation)
        path = await asyncio.to_thread(shots.save, data)
        copied = True
        try:
            await asyncio.to_thread(pcclip.set_image, data)
        except Exception as exc:
            copied = False
            log.warning("screenshot not copied to the clipboard: %r", exc)
        log.info("screenshot saved (rotation %d)", rotation)
        return {"ok": True, "path": str(path), "name": path.name, "folder": str(path.parent), "copied": copied}

    def shot_folder_info() -> dict:
        from . import shots
        return {"path": str(shots.folder()), "default": str(shots.default_folder()),
                "custom": shots.custom_dir is not None}

    async def shot_folder(request, body):
        """GET: where screenshots go. POST {"action": "pick"|"reset"}: change it,
        from the iCtrl window only (agents do not change settings)."""
        if request.method == "GET":
            return await asyncio.to_thread(shot_folder_info)
        if agent_request.get():
            return error("保存先の変更は iCtrl の画面から行ってください", 403)
        from . import shots
        action = body.get("action")
        if action == "reset":
            await manager.update_settings({"screenshot_dir": None})
        elif action == "pick":
            owner = shots.foreground_window()
            chosen = await asyncio.to_thread(shots.pick_folder, owner, shots.folder())
            if chosen is None:
                return {**await asyncio.to_thread(shot_folder_info), "cancelled": True}
            await manager.update_settings({"screenshot_dir": str(chosen)})
        else:
            return error("unknown action", 404)
        return await asyncio.to_thread(shot_folder_info)

    async def shot_last(_request, _body):
        """The last saved screenshot file itself (the browser scales it for the
        message shown after saving)."""
        from . import shots
        if shots.last_saved is None or not shots.last_saved.exists():
            return error("保存したスクリーンショットがありません", 404)
        return FileResponse(shots.last_saved, media_type="image/png", headers={"Cache-Control": "no-store"})

    async def shot_reveal(_request, _body):
        from . import shots
        await asyncio.to_thread(shots.reveal, shots.foreground_window())
        return {"ok": True}

    async def apps(_request, _body):
        return {"apps": await manager.require().list_apps()}

    async def launch(_request, body):
        agent("launch", bundle_id=body["bundle_id"])
        return await manager.require().launch_app(body["bundle_id"])

    async def clipboard(request, body):
        """GET: the iPhone clipboard now (also added to the history).
        POST {"text"} or {"image_b64"}: put it on the iPhone clipboard."""
        session = manager.require()
        if request.method == "POST":
            if "image_b64" in body:
                await session.set_clipboard_image(base64.b64decode(body["image_b64"]))
            else:
                await session.set_clipboard(str(body["text"]))
            return {"ok": True}
        from .clipboard import content_parts
        text, png = await asyncio.to_thread(content_parts, await session.read_clipboard())
        item = None
        if text or png:
            item = await asyncio.to_thread(manager.clips.add, "iphone", text, png)
        return {"text": text, "image": bool(png), "item": item.to_json() if item else None}

    def _shape(value, depth=0):
        if isinstance(value, dict) and depth < 3:
            return {k: _shape(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (bytes, str)):
            return f"{type(value).__name__}[{len(value)}]"
        return type(value).__name__

    async def clipboard_types(_request, _body):
        """Diagnostics: which representations the iPhone pasteboard offers
        and which ones carry data inline (no content is returned)."""
        from pymobiledevice3.remote.core_device.pasteboard_service import (
            POLICY_PROMISE_SECONDARY, PasteboardService)
        session = manager.require()
        from pymobiledevice3.remote.core_device.pasteboard_service import POLICY_ALL_RESOLVED, POLICY_MATCH_SOURCE
        policy = {"all": POLICY_ALL_RESOLVED, "match": POLICY_MATCH_SOURCE}.get(
            _request.query_params.get("policy"), POLICY_PROMISE_SECONDARY)
        started = time.monotonic()
        async with PasteboardService(session.rsd) as pb:
            snap = await asyncio.wait_for(pb.get("general", policy), 90)
        elapsed = time.monotonic() - started
        board = snap.get("pasteboard", snap)
        items = []
        for item in board.get("items", []) or []:
            data = item.get("data") or {}
            items.append({"types": item.get("types"),
                          "inline": {k: _shape(v) for k, v in data.items()}})
        return {"items": items, "seconds": round(elapsed, 2)}

    async def clipboard_resolve(request, _body):
        """Diagnostics: time one RESOLVE of one type (?type=...&index=0);
        ?auto=0/1 pauses/resumes automatic resolution of iPhone copies."""
        from . import clipboard as cb
        if "auto" in request.query_params:
            cb.RESOLVE_ENABLED[0] = request.query_params["auto"] == "1"
            return {"auto": cb.RESOLVE_ENABLED[0]}
        session = manager.require()
        uti = request.query_params["type"]
        started = time.monotonic()
        try:
            data = await asyncio.wait_for(cb.resolve(session.rsd, "general",
                                                     int(request.query_params.get("index", 0)), uti), 80)
            return {"type": uti, "seconds": round(time.monotonic() - started, 2),
                    "bytes": len(data) if data else 0}
        except asyncio.TimeoutError:
            return {"type": uti, "seconds": None, "timeout": True}

    async def wda_call(make):
        from .wda import WdaError
        try:
            return await make(manager.require().wda())
        except WdaError as error:
            raise SessionError(str(error)) from error

    LOCKED_MESSAGE = "iPhone はロック中です。ロック解除はユーザーに依頼してください（パスコードは入力しません）"

    async def refuse_when_locked(session, listed: dict | None = None) -> None:
        """Agents never type a passcode: no element taps while the phone is
        locked or shows the passcode screen."""
        from .wda import WdaError
        try:
            locked = await session.wda().locked()
            if listed is None:
                listed = await session.wda().elements()
        except WdaError:
            return
        if locked or listed.get("passcode"):
            raise SessionError(LOCKED_MESSAGE)

    async def read_elements(session) -> dict:
        """Elements from WebDriverAgent, or from OCR when WDA is off
        (wda_mode "off") or cannot start; the OCR result says so."""
        from .screentext import ocr_elements
        from .wda import WdaError
        if getattr(session, "wda_mode", "on_demand") == "off":
            return await ocr_elements(session)
        try:
            w = session.wda()
            result, locked = await asyncio.gather(w.elements(), w.locked())
        except WdaError as error:
            result = await ocr_elements(session)
            result["wda_error"] = str(error)
            return result
        result["locked"] = locked = locked or result.pop("passcode", False)
        if locked:
            # The lock screen lists the passcode keypad; agents must not
            # enter a passcode, so its elements are not handed out.
            result["elements"] = []
        result["source"] = "wda"
        return result

    async def elements(_request, _body):
        """Labelled elements on screen: type, label, the centre and size in
        0..1 of the screen, plus lock state and any alert (system alerts are
        not part of the app's tree). WebDriverAgent, or OCR (see read_elements)."""
        return await read_elements(manager.require())

    async def wait_for(_request, body):
        """Until an element labelled body["text"] appears (or with "gone",
        disappears), within body["timeout"] seconds (default 10, max 60).
        Reads the screen back to back; the AI otherwise loops screenshots."""
        from .wda import WdaError, find_in
        text = str(body["text"])
        gone = bool(body.get("gone"))
        timeout = max(0.5, min(60.0, float(body.get("timeout", 10))))
        session = manager.require()
        started = time.perf_counter()
        reads = 0
        while True:
            found = (await read_elements(session))["elements"]
            reads += 1
            match = None
            try:
                match = find_in(found, text, 0)
            except WdaError:
                pass
            if (match is None) == gone:
                return {"ok": True, "found": not gone, "element": match, "reads": reads,
                        "seconds": round(time.perf_counter() - started, 2)}
            if time.perf_counter() - started >= timeout:
                return {"ok": False, "found": match is not None, "reads": reads,
                        "error": f"{timeout:g} 秒待っても「{text}」が{'消え' if gone else '現れ'}ませんでした"}
            await asyncio.sleep(0.1)

    async def scroll_to(_request, body):
        """Scroll until an element labelled body["text"] is on screen.
        direction: down (default; reveals content below) / up / left / right.
        Stops at the end of the content (the screen stopped changing)."""
        from .wda import WdaError
        text = str(body["text"])
        direction = body.get("direction", "down")
        limit = max(1, min(30, int(body.get("max", 12))))
        strokes = {"down": (0.5, 0.72, 0.5, 0.32), "up": (0.5, 0.32, 0.5, 0.72),
                   "right": (0.8, 0.5, 0.2, 0.5), "left": (0.2, 0.5, 0.8, 0.5)}
        if direction not in strokes:
            raise ValueError("direction は down / up / left / right です")
        session = manager.require()
        await refuse_when_locked(session)          # a swipe on the lock screen opens the keypad
        w = session.wda()
        previous = None
        for swipes in range(limit + 1):
            try:
                found = (await w.elements())["elements"]
                return {"ok": True, "element": await w.find(text, 0, found), "swipes": swipes}
            except WdaError as error:
                if "見つかりません" not in str(error):
                    raise SessionError(str(error)) from error
            labels = [(e["label"], round(e["x"], 2), round(e["y"], 2)) for e in found]
            if labels == previous:
                return {"ok": False, "swipes": swipes, "error": f"端までスクロールしても「{text}」は見つかりませんでした"}
            previous = labels
            if swipes == limit:
                break
            x1, y1, x2, y2 = strokes[direction]
            agent("swipe", x1=x1, y1=y1, x2=x2, y2=y2, duration=0.3)
            # The finger rests before lifting: no momentum, so the screen read
            # next is where the list stopped.
            await session.run({"op": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": 0.3,
                               "settle": True})
        return {"ok": False, "swipes": limit, "error": f"{limit} 回スクロールしても「{text}」は見つかりませんでした"}

    async def alert(request, body):
        """GET: the alert on screen. POST {"action": "accept"|"dismiss", "name"?}."""
        if request.method == "GET":
            return {"alert": await wda_call(lambda w: w.alert())}
        return await wda_call(lambda w: w.alert_action(str(body.get("action", "accept")), body.get("name")))

    def from_person(request) -> None:
        """Passcode actions are for the person at this PC only: the iCtrl
        window (a browser on this origin), not exposed as an agent tool."""
        if from_agent(request.headers) or request.headers.get("origin") not in allowed_origins:
            raise PermissionError("パスコードの操作は iCtrl のウィンドウからだけ行えます（AI からは使えません）")

    async def passcode_route(request, body):
        from . import passcode
        if request.method == "GET":
            session = manager.session
            udid = session.info.udid if session and session.ready else None
            return {"available": passcode.available(),
                    "saved": await asyncio.to_thread(passcode.is_saved),
                    "automatic": await asyncio.to_thread(passcode.auto_status, udid)}
        from_person(request)
        action = request.path_params["action"]
        if action not in {"save", "delete", "enter", "automatic"}:
            return error("このパスコード操作は利用できません", 404)
        try:
            if action == "save":
                await passcode.save(str(body.get("passcode", "")))
                manager.notify({"t": "passcode"})
                return {"ok": True}
            if action == "delete":
                await passcode.delete()
                manager.notify({"t": "passcode"})
                return {"ok": True}
            if action == "enter":
                if not isinstance(body, dict) or any(
                        key in body for key in ("confirmed_on_device", "open_lock_screen")):
                    raise ValueError("XCTest 以外のパスコード入力には対応していません")
                return await passcode.enter(manager.require())
            if action == "automatic":
                if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
                    raise ValueError("enabled must be true or false")
                if "other_prompts" in body:
                    raise ValueError("自動入力の対象は XCTest の要求だけです")
                enabled = body["enabled"]
                result = await passcode.configure_automatic(manager.require() if enabled else None, enabled)
                manager.notify({"t": "passcode"})
                return result
        except passcode.PasscodeError as exc:
            raise SessionError(str(exc)) from None
        raise KeyError(action)

    async def automation_stop(_request, body):
        """End WebDriverAgent now (also one opened from the home screen), which
        removes iOS's "Automation Running" banner. It starts again when an
        agent next asks for screen elements. An agent finishing its work
        """
        # Not connected: there is nothing to end (an agent's finish must not fail).
        session = manager.session
        if session is not None and session.ready:
            await session.wda().stop()
        return {"ok": True}

    async def open_url(_request, body):
        agent("launch", bundle_id=str(body["url"])[:60])
        return await wda_call(lambda w: w.open_url(str(body["url"])))

    async def terminate(_request, body):
        return await wda_call(lambda w: w.terminate(str(body["bundle_id"])))

    async def pinch(_request, body):
        return await wda_call(lambda w: w.pinch(float(body.get("scale", 2.0)),
                                                body.get("velocity")))

    async def rotate(_request, body):
        return await wda_call(lambda w: w.rotate(float(body.get("degrees", 90))))

    async def orientation(request, body):
        if request.method == "GET":
            return {"orientation": await wda_call(lambda w: w.orientation())}
        return await wda_call(lambda w: w.set_orientation(str(body["orientation"])))

    last_marks: dict = {}

    async def screenshot_marks(request, _body):
        """Screenshot with every element boxed and numbered (for agents that
        work from images); /api/tap_mark taps a number."""
        session = manager.require()
        max_side = int(request.query_params.get("max", "1024") or 1024)
        listed = await wda_call(lambda w: w.elements())
        data, width, height = await session.screen_png(max_side)
        image = await asyncio.to_thread(draw_marks, data, listed["elements"])
        marks = [{"n": i + 1, "type": e["type"], "label": e["label"], "x": e["x"], "y": e["y"]}
                 for i, e in enumerate(listed["elements"])]
        last_marks["list"] = marks
        return {"image_b64": base64.b64encode(image).decode(), "width": width, "height": height, "marks": marks}

    async def tap_mark(_request, body):
        marks = last_marks.get("list") or []
        n = int(body["n"])
        if not 1 <= n <= len(marks):
            raise ValueError(f"番号 {n} はありません（直前の番号付きスクリーンショットは {len(marks)} 個）")
        mark = marks[n - 1]
        session = manager.require()
        # The screen may have changed (or locked) since the numbered
        # screenshot: tap only if the same element is still in that place.
        listed = await wda_call(lambda w: w.elements())
        await refuse_when_locked(session, listed)
        if not any(e["label"] == mark["label"] and abs(e["x"] - mark["x"]) < 0.03 and abs(e["y"] - mark["y"]) < 0.03
                   for e in listed["elements"]):
            raise SessionError(f"画面が変わったため {n} 番（{mark['label']}）は押しませんでした。番号付きスクリーンショットを撮り直してください")
        agent("tap", x=mark["x"], y=mark["y"])
        await manager.require().run({"op": "tap", "x": mark["x"], "y": mark["y"], "hold": 0.05})
        return {"ok": True, "tapped": mark}

    async def tap_element(_request, body):
        """Tap the element whose label is (or contains) body["text"]. A list
        taps several in order from one reading of the screen (a keypad)."""
        from .wda import WdaError, find_in
        session = manager.require()
        texts = body["text"] if isinstance(body["text"], list) else [body["text"]]
        tapped = []
        listed = await read_elements(session)
        if listed.get("locked"):
            raise SessionError(LOCKED_MESSAGE)
        try:
            targets = [find_in(listed["elements"], str(t), int(body.get("index", 0))) for t in texts]
        except WdaError as error:
            raise SessionError(str(error)) from error
        if len(targets) == 1 and _obstructed(targets[0], listed["elements"]):
            # Below a floating bar (iOS 27's search field) or in the bottom
            # strip, where a touch starts the home gesture: the tap reached
            # nothing (Settings' 一般). Scroll it into view first.
            text = str(texts[0])
            for _ in range(3):
                await session.run({"op": "swipe", "x1": 0.5, "y1": 0.7, "x2": 0.5, "y2": 0.45,
                                   "duration": 0.3, "settle": True})
                listed = await read_elements(session)
                try:
                    targets = [find_in(listed["elements"], text, int(body.get("index", 0)))]
                except WdaError as error:
                    raise SessionError(str(error)) from error
                if not _obstructed(targets[0], listed["elements"]):
                    break
            else:
                raise SessionError(f"「{text}」が画面の下のバーに隠れていて押せません")
        for element in targets:
            agent("tap", x=element["x"], y=element["y"])
            await session.run({"op": "tap", "x": element["x"], "y": element["y"], "hold": 0.05})
            tapped.append(element)
            if len(targets) > 1:
                await asyncio.sleep(0.08)       # let the app take each press as a separate tap
        return {"ok": True, "tapped": tapped[0] if len(tapped) == 1 else tapped}

    async def pairing_create(_request, _body):
        """Create iCtrl's own pairing (the iPhone asks to trust "iCtrl").
        Takes effect from the next connection."""
        from . import pairing
        session = manager.session
        udid = session.info.udid if session is not None else None
        if udid is None:
            raise SessionError("先に USB で接続してください")
        manager.notify({"t": "pairing", "state": "waiting"})
        try:
            result = await pairing.create(udid)
        except Exception as error:
            manager.notify({"t": "pairing", "state": "failed", "error": str(error)})
            raise SessionError(f"ペアリングできませんでした: {type(error).__name__}: {error}") from error
        manager.notify({"t": "pairing", "state": "done"})
        return result

    async def plain_copy(_request, _body):
        """Run the iPhone 'ictrlcopy' shortcut now (diagnostics / manual)."""
        return await manager.require().trigger_plain_copy()

    async def clipboard_pull(_request, _body):
        """iPhone clipboard -> PC clipboard, now."""
        result = await clipboard(_Get(), {})
        if not result["item"]:
            return {"ok": False, "error": "iPhone のクリップボードは空です（または対応していない形式です）"}
        await asyncio.to_thread(manager.copy_item_to_pc, manager.clips.get(result["item"]["id"]))
        return {"ok": True, **result}

    async def clipboard_push(_request, _body):
        """PC clipboard (text or image) -> iPhone clipboard, now."""
        from . import pcclip
        session = manager.require()
        pc = await asyncio.to_thread(pcclip.get)
        if (pc.get("text") or "").strip():
            await session.set_clipboard(pc["text"])
            kind = "text"
        elif pc.get("png"):
            await session.set_clipboard_image(pc["png"])
            kind = "image"
        else:
            return {"ok": False, "error": "PC のクリップボードは空です"}
        item = await asyncio.to_thread(manager.clips.add, "pc", pc.get("text") if kind == "text" else None,
                                       pc.get("png"))
        if item:
            manager.notify({"t": "clipboard", "item": item.to_json(), "copied_to_pc": False})
        return {"ok": True, "kind": kind}

    async def clipboard_history(_request, _body):
        return {"items": [i.to_json() for i in manager.clips.items],
                "auto_copy_to_pc": manager.settings.get("auto_copy_to_pc", True),
                "clipboard_shortcut_mode": manager.settings.get("clipboard_shortcut_mode", "keyboard")}

    async def clipboard_item(request, _body):
        item = manager.clips.get(int(request.path_params["item_id"]))
        if item is None:
            return error("履歴にありません", 404)
        action = request.path_params.get("action")
        if request.method == "GET":
            if item.png:
                return Response(item.png, media_type="image/png")
            return {"text": item.text}
        if action == "to-pc":
            await asyncio.to_thread(manager.copy_item_to_pc, item)
        elif action == "to-iphone":
            session = manager.require()
            if item.png:
                await session.set_clipboard_image(item.png)
            else:
                await session.set_clipboard(item.text or "")
        else:
            return error("unknown action", 404)
        return {"ok": True}

    async def settings(request, body):
        if request.method == "POST":
            if "screenshot_dir" in body and agent_request.get():
                return error("保存先の変更は iCtrl の画面から行ってください", 403)
            return await manager.update_settings(body)
        return manager.settings

    async def keyframe(_request, _body):
        manager.require().request_keyframe("api")
        return {"ok": True}

    async def ws(websocket: WebSocket):
        if not same_origin(websocket.headers):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_text(json.dumps({"t": "hello", "build": ui_build()}))
        log.info("viewer connected")
        window = not from_agent(websocket.headers)       # an iCtrl page, not a script
        # The app window (iCtrlWindow.exe marks its user agent) cannot reload,
        # so its page closing means the window closed; the page itself says
        # when it reloads for a newer GUI.
        app_window = "iCtrlWindow" in websocket.headers.get("user-agent", "")
        reloading = False
        shown = False       # this page is on screen (it asks for video exactly then)
        if window:
            manager.page_opened()
        events = manager.listen()
        video_task: asyncio.Task | None = None
        tasks: set[asyncio.Task] = set()

        async def pump_status():
            while True:
                await websocket.send_text(json.dumps(await events.get(), ensure_ascii=False))

        async def pump_video(mode: str):
            # Follows session changes: a new session gets a new hub.
            while True:
                session = manager.session
                if session is None or not session.ready:
                    await events_changed.wait()
                    continue
                sub = session.hub.subscribe()
                try:
                    while True:
                        kind, item = await sub.queue.get()
                        if kind == "end":
                            await websocket.send_text('{"t":"video-end"}')
                            break
                        if kind == "config":
                            await websocket.send_text(json.dumps(
                                {**item.to_json(session.info.screen), "mode": "webcodecs"}))
                        else:
                            await websocket.send_bytes(unit_message(item))
                finally:
                    session.hub.unsubscribe(sub)

        events_changed = asyncio.Event()

        agent_socket = from_agent(websocket.headers)

        async def handle(message: dict):
            nonlocal video_task, shown, reloading
            t = message.get("t")
            if t == "reloading":
                reloading = True
                return
            if t == "video":
                # A page asks for video while it is on screen and stops when
                # hidden (minimized, a background tab): that is "in use".
                on = bool(message.get("on", True))
                if window and on != shown:
                    shown = on
                    manager.window_opened(app_window) if on else manager.window_closed(app_window)
                if video_task:
                    video_task.cancel()
                    video_task = None
                if message.get("on", True):
                    video_task = asyncio.create_task(pump_video(message.get("mode", "webcodecs")))
                return
            if t == "ime_key":
                # The page saw an IME key (半角/全角 ...): read the IME once.
                if getattr(manager, "ime_watch", None):
                    manager.ime_watch.notice()
                return
            if t == "client":
                log.info("viewer %s", {k: message.get(k) for k in
                         ("mode", "hw", "format", "frame", "ua")
                         if k != "hw" or message.get("mode") != "key"})
                return
            if t == "keyframe":
                if manager.session:
                    manager.session.request_keyframe("viewer-" + str(message.get("reason", "?"))[:20])
                return
            session = manager.session
            if session is None or not session.ready:
                return
            ops = {
                "touch": lambda m: {"op": "touch", "phase": m["phase"], "x": m["x"], "y": m["y"]},
                "button": lambda m: {"op": "button", "name": m["name"], "state": m.get("state", "press")},
                "keys": lambda m: {"op": "keys", "usages": m.get("usages", [])},
                "text": lambda m: {"op": "text", "text": m["text"]},
                "key": lambda m: {"op": "key", "key": m["key"], "modifiers": m.get("modifiers", [])},
                "release": lambda m: {"op": "release"},
                "lock": lambda m: {"op": "lock"},
                "pc_keyboard": lambda m: {"op": "pc_keyboard", "on": bool(m.get("on", True))},
            }
            if t in ops:
                if agent_socket:
                    # A script driving the phone over the socket, not the GUI.
                    manager.notify({"t": "agent", "phase": "pulse", "op": t})
                elif window and t in ("touch", "button", "keys", "text", "key", "lock"):
                    manager.person_used()          # not pc_keyboard / release: the page sends those itself
                relinked = t == "pc_keyboard" and message.get("on", True) and not session.pc_keyboard
                future = session.submit(ops[t](message))
                if relinked:
                    # Linked again (not every time a window regains the focus
                    # and restates it): the iPhone takes the Windows IME's mode.
                    asyncio.get_running_loop().create_task(manager.follow_windows_ime("keyboard", force=True))
                if message.get("id") is not None:
                    async def reply(fut=future, ident=message["id"]):
                        try:
                            result = {"ok": True, "result": await fut}
                        except Exception as exc:
                            result = {"ok": False, "error": str(exc)}
                        await websocket.send_text(json.dumps({"t": "reply", "id": ident, **result},
                                                             ensure_ascii=False, default=str))
                    task = asyncio.create_task(reply())
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                else:
                    future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)

        status_task = asyncio.create_task(pump_status())

        async def watch_events():
            # Wake video pumps when sessions change.
            nonlocal events_changed
            q = manager.listen()
            try:
                while True:
                    await q.get()
                    events_changed.set()
                    events_changed = asyncio.Event()
            finally:
                manager.unlisten(q)

        watcher = asyncio.create_task(watch_events())
        try:
            while True:
                raw = await websocket.receive_text()
                with contextlib.suppress(ValueError, KeyError, TypeError, SessionError):
                    await handle(json.loads(raw))
        except WebSocketDisconnect:
            pass
        finally:
            if shown:
                manager.window_closed(app_window)
            if window:
                manager.page_closed()
            if app_window and not reloading:
                manager.app_window_closed()
            manager.unlisten(events)
            for task in (status_task, watcher, video_task, *tasks):
                if task:
                    task.cancel()

    routes = [
        Route("/", index),
        Route("/api/ping", api(ping)),
        Route("/api/status", api(status)),
        Route("/api/ime", api(ime), methods=["GET", "POST"]),
        Route("/api/setup", api(setup)),
        Route("/api/devices", api(devices)),
        Route("/api/reachable", api(reachable)),
        Route("/api/connect", api(connect), methods=["POST"]),
        Route("/api/disconnect", api(disconnect), methods=["POST"]),
        Route("/api/tap", api(tap), methods=["POST"]),
        Route("/api/swipe", api(swipe), methods=["POST"]),
        Route("/api/button", api(button), methods=["POST"]),
        Route("/api/text", api(text), methods=["POST"]),
        Route("/api/key", api(key), methods=["POST"]),
        Route("/api/screenshot", api(shot)),
        Route("/api/screenshot/copy", api(shot_copy), methods=["POST"]),
        Route("/api/screenshot/save", api(shot_save), methods=["POST"]),
        Route("/api/screenshot/reveal", api(shot_reveal), methods=["POST"]),
        Route("/api/screenshot/last", api(shot_last)),
        Route("/api/screenshot/folder", api(shot_folder), methods=["GET", "POST"]),
        Route("/api/apps", api(apps)),
        Route("/api/launch", api(launch), methods=["POST"]),
        Route("/api/clipboard", api(clipboard), methods=["GET", "POST"]),
        Route("/api/clipboard/pull", api(clipboard_pull), methods=["POST"]),
        Route("/api/clipboard/types", api(clipboard_types)),
        Route("/api/clipboard/resolve", api(clipboard_resolve)),
        Route("/api/clipboard/plain", api(plain_copy), methods=["POST"]),
        Route("/api/elements", api(elements)),
        Route("/api/video/start", api(video_start), methods=["POST"]),
        Route("/api/video/stop", api(video_stop), methods=["POST"]),
        Route("/api/pairing", api(pairing_create), methods=["POST"]),
        Route("/api/wait_for", api(wait_for), methods=["POST"]),
        Route("/api/scroll_to", api(scroll_to), methods=["POST"]),
        Route("/api/alert", api(alert), methods=["GET", "POST"]),
        Route("/api/open_url", api(open_url), methods=["POST"]),
        Route("/api/automation/stop", api(automation_stop), methods=["POST"]),
        Route("/api/passcode", api(passcode_route)),
        Route("/api/passcode/{action}", api(passcode_route), methods=["POST"]),
        Route("/api/terminate", api(terminate), methods=["POST"]),
        Route("/api/pinch", api(pinch), methods=["POST"]),
        Route("/api/rotate", api(rotate), methods=["POST"]),
        Route("/api/orientation", api(orientation), methods=["GET", "POST"]),
        Route("/api/screenshot_marks", api(screenshot_marks)),
        Route("/api/tap_mark", api(tap_mark), methods=["POST"]),
        Route("/api/tap_element", api(tap_element), methods=["POST"]),
        Route("/api/clipboard/push", api(clipboard_push), methods=["POST"]),
        Route("/api/clipboard/history", api(clipboard_history)),
        Route("/api/clipboard/item/{item_id:int}", api(clipboard_item)),
        Route("/api/clipboard/item/{item_id:int}/{action}", api(clipboard_item), methods=["POST"]),
        Route("/api/settings", api(settings), methods=["GET", "POST"]),
        Route("/api/keyframe", api(keyframe), methods=["POST"]),
        WebSocketRoute("/ws", ws),
        Mount("/ui", StaticFiles(directory=UI_DIR), name="ui"),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        loop = asyncio.get_running_loop()
        default = loop.get_exception_handler()

        def quiet_resets(loop_, context):
            # Windows Proactor reports a peer reset during socket teardown as
            # an unhandled callback error. The connection is already gone;
            # nothing is lost, so keep it out of the error log.
            exc = context.get("exception")
            gone = isinstance(exc, ConnectionResetError) or getattr(exc, "winerror", None) == 10022
            if gone and "_call_connection_lost" in str(context.get("handle", "")):
                return
            if default is None:
                loop_.default_exception_handler(context)
            else:
                default(loop_, context)

        loop.set_exception_handler(quiet_resets)
        from .pipe import PipeServer
        pipe_server = PipeServer(_app, loop)
        pipe_server.start()
        _retire_startup_shortcut()
        ime_watch = None
        if os.name == "nt":
            from .imewatch import ImeWatcher
            ime_watch = ImeWatcher(manager.windows_ime_changed)
            ime_watch.start()
            manager.ime_watch = ime_watch

        async def end_unused():
            # Nothing uses the server: clean up and end the process here.
            # uvicorn's graceful shutdown (Ctrl+C) waited for a page's socket
            # that never closed (the Claude app's pane asleep overnight): the
            # HTTP port closed while the process and its pipe stayed alive
            #. Nobody is connected, so nobody waits for an answer.
            if ime_watch:
                await ime_watch.stop()
            pipe_server.stop()
            await manager.shutdown()
            log.info("server ended (not in use)")
            logging.shutdown()
            os._exit(0)

        manager.on_unused = lambda: loop.create_task(end_unused())
        manager.arm_exit()                    # started but never used
        yield
        if ime_watch:
            await ime_watch.stop()
        pipe_server.stop()
        await manager.shutdown()

    class NoCache:
        """UI files change with the app; always revalidate them."""
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send_):
            if scope["type"] != "http" or not scope["path"].startswith("/ui/"):
                return await self.inner(scope, receive, send_)

            async def wrapped(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", [])
                    message["headers"] = [h for h in message["headers"] if h[0] != b"cache-control"]
                    message["headers"].append((b"cache-control", b"no-cache"))
                await send_(message)
            await self.inner(scope, receive, wrapped)

    from .localauth import SameUserMiddleware
    app = Starlette(routes=routes, lifespan=lifespan, middleware=[
        Middleware(SameUserMiddleware, server_port=port),
        Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"]),
        Middleware(NoCache),
    ])
    app.state.manager = manager
    return app
