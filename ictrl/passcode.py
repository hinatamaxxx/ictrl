"""Local XCTest passcode entry, from the window or during WDA startup.

The automatic grant belongs to one iPhone, is consumed before sending, and
is rearmed only when WDA starts. A failed entry therefore stays blocked
across reconnects and process restarts. Secrets and
grants live in Windows Credential Manager, never in settings or MCP.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

log = logging.getLogger(__name__)

TARGET = "iCtrl/iPhone passcode"
AUTO_TARGET = "iCtrl/XCTest automatic entry"
_VALID = re.compile(r"^[A-Za-z0-9]{4,32}$")
# OCR reads パ as バ at times (the XCTest prompt read "iPhoneのバスコードを入力");
# "Enable UI Automation" is the same prompt's subtitle.
# Windows OCR also reads the small Japanese glyphs as 八スコ-ド.
_PASSCODE_WORD = r"[パバハ八]スコ[ーｰ－–—−-]ド"
_entering = False


class PasscodeError(Exception):
    """A refusal or failure, with a message that never contains the passcode."""


# ------------------------------------------------------------------ availability
def available() -> bool:
    try:
        import win32cred
        return True
    except ImportError:
        return False


# ------------------------------------------------------------------ storage
def is_saved() -> bool:
    return _read() is not None


def _read_credential(target: str) -> str | None:
    import pywintypes
    import win32cred
    try:
        try:
            cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error as error:
            if error.winerror == 1168:  # ERROR_NOT_FOUND
                return None
            raise
        blob = cred.get("CredentialBlob") or b""
        return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
    except Exception:
        raise PasscodeError("Windows 資格情報を読み取れませんでした") from None


def _read() -> str | None:
    return _read_credential(TARGET)


def _write_credential(target: str, value: str) -> None:
    import win32cred
    try:
        win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": target,
                             "UserName": "iCtrl", "CredentialBlob": value,
                             "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)
    except Exception:
        raise PasscodeError("Windows 資格情報を保存できませんでした") from None


def _delete_credential(target: str) -> None:
    import pywintypes
    import win32cred
    try:
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
    except pywintypes.error as error:
        if error.winerror != 1168:
            raise PasscodeError("Windows 資格情報を削除できませんでした") from None


async def save(passcode: str) -> None:
    if _entering:
        raise PasscodeError("パスコードを入力中です")
    if not _VALID.fullmatch(passcode or ""):
        raise PasscodeError("パスコードは英数字 4〜32 文字で指定してください")
    _delete_credential(AUTO_TARGET)
    _write_credential(TARGET, passcode)
    log.info("passcode: saved")


async def delete() -> None:
    if _entering:
        raise PasscodeError("パスコードを入力中です")
    _delete_credential(AUTO_TARGET)
    _delete_credential(TARGET)
    log.info("passcode: deleted")


# ------------------------------------------------------------------ entry
async def shows_prompt(session) -> bool:
    """Recognize only XCTest through a fresh accessibility focus cycle."""
    from .xctest_prompt import read_prompt
    return await read_prompt(session)


def strokes(passcode: str) -> list[tuple[int, bool]]:
    from . import keys
    return [keys.CHARS[c] for c in passcode] + [keys.CHARS["\n"]]


async def enter(session) -> dict:
    """Type once on a strictly recognized XCTest request from the app window."""
    if _entering:
        raise PasscodeError("パスコードを入力中です")
    return await _enter_manual(session)


async def _enter_manual(session) -> dict:
    entry = _ManualEntry(session)
    try:
        await entry._send()
    except Exception as error:
        log.info("passcode: entry failed (%s)", type(error).__name__)
        raise PasscodeError("パスコードの入力に失敗しました（再試行はしません）") from None
    finally:
        entry._active = False
        if entry._consumed is None:
            session._set(passcode_state="idle")
    log.info("passcode: entered on request from the app window")
    return {"ok": True}


# ------------------------------------------------------------------ automatic grant
def _read_auto() -> dict | None:
    raw = _read_credential(AUTO_TARGET)
    if raw is None:
        return None
    try:
        grant = json.loads(raw)
        if (not isinstance(grant, dict) or not isinstance(grant.get("udid"), str)
                or not grant["udid"] or not isinstance(grant.get("token"), str)
                or not grant["token"] or type(grant.get("blocked")) is not bool):
            raise ValueError()
        if "other_prompts" in grant:
            # Retire the former device/app-unlock scope without rearming an
            # already consumed XCTest grant or changing its identity.
            del grant["other_prompts"]
            _write_credential(AUTO_TARGET, json.dumps(grant))
        return grant
    except (ValueError, TypeError):
        raise PasscodeError("自動入力の設定を読み取れません。ウィンドウで設定し直してください") from None


def auto_status(udid: str | None = None) -> dict:
    grant = _read_auto()
    return {"enabled": grant is not None, "blocked": bool(grant and grant["blocked"]),
            "device_matches": bool(grant and udid and grant["udid"] == udid)}


async def configure_automatic(session, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise PasscodeError("enabled は true / false で指定してください")
    if not enabled:
        _delete_credential(AUTO_TARGET)
    else:
        if session is None or not session.ready:
            raise PasscodeError("自動入力を使う iPhone に接続してください")
        if _entering:
            raise PasscodeError("パスコードを入力中です")
        if not is_saved():
            raise PasscodeError("先にパスコードを保存してください")
        _write_credential(AUTO_TARGET, json.dumps({"udid": session.info.udid,
                          "token": uuid.uuid4().hex, "blocked": False}))
    if session is not None:
        session._set(passcode_state="idle")
    return {"ok": True, "automatic": auto_status(session.info.udid if session else None)}


def has_entered_passcode(lines: list[dict]) -> bool:
    """Refuse visible masked input without reading or logging its contents.

    Empty rings alone are not entered digits. Missing OCR marks are also not
    proof of an empty field: secure controls may be omitted from recognition.
    """
    filled = "●•∙·⦿◉⚫⬤*＊"
    rings = "○◦〇◯◎◌"
    for line in lines:
        text = re.sub(r"\s+", "", str(line.get("text", "")))
        # A single filled field mark, or a run of field marks embedded in a
        # label. Restrict middot/star matches to runs to avoid prose bullets.
        if (text and all(char in filled + rings for char in text)
                and any(char in filled for char in text)):
            return True
        for marks in re.findall(r"[" + re.escape(filled + rings) + r"]{2,}", text):
            if any(char in filled for char in marks):
                return True
        # Some secure fields briefly show their final character beside rings.
        for marks in re.findall(r"[0-9" + re.escape(rings) + r"]{2,}", text):
            if any(char.isdigit() for char in marks) and any(char in rings for char in marks):
                return True
    return False


def is_xctest_prompt(lines: list[dict], size: tuple[int, int] | None = None) -> bool:
    """Recognize an XCTest heading with its Cancel control or automation subtitle.

    Searching the whole screen for scattered words also matched help pages.
    Instead accept only a short heading, including adjacent OCR line wraps.
    Screen geometry is a coarse sanity check, not proof of a system UI: a
    page deliberately copying the same prompt is indistinguishable by OCR.
    """
    from math import isfinite

    if has_entered_passcode(lines):
        return False
    rows = [(line, re.sub(r"\s+", "", str(line.get("text", ""))).lower())
            for line in lines if str(line.get("text", "")).strip()]
    text = "".join(value for _, value in rows)
    if "xctest" not in text or re.search(
            r"やり直|正しく|間違|失敗|使用できません|incorrect|wrong|failed|tryagain|unavailable|tryin", text):
        return False

    def box(line):
        try:
            x, y, width, height = (float(line[key]) for key in ("x", "y", "w", "h"))
            screen_w, screen_h = size
            if (not all(isfinite(v) for v in (x, y, width, height, screen_w, screen_h))
                    or screen_w <= 0 or screen_h <= 0 or width <= 0 or height <= 0
                    or x < 0 or y < 0 or x + width > screen_w or y + height > screen_h):
                return None
            return x, y, x + width, y + height
        except (KeyError, TypeError, ValueError):
            return None

    cancels = [line for line, value in rows if value in ("キャンセル", "cancel")]
    subtitles = [line for line, value in rows if value == "enableuiautomation"]
    if not cancels and not subtitles:
        return False
    if size is not None:
        # Windows OCR can return the lower Japanese heading line first.
        # Use screen order instead of assuming recognition order is visual order.
        rows.sort(key=lambda row: (box(row[0]) or (0, float("inf"), 0, 0))[1])
        cancels = [line for line in cancels if box(line)]
        subtitles = [line for line in subtitles if box(line)]
        if not cancels and not subtitles:
            return False
    name = r'''[「『“"'‘]?xctest[」』”"'’]?'''
    jp_purpose = name + r"を(?:使うため|使用するため|有効にするため)に?"
    jp_entry = r"iphoneの" + _PASSCODE_WORD + r"を入力(?:してください)?"
    en_purpose = r"to(?:use|enable)" + name
    en_entry = r"enter(?:your|the)?(?:iphone)?passcode"
    heading = re.compile(
        rf"(?:{jp_purpose}[、,]?{jp_entry}|{jp_entry}[、,]?{jp_purpose}|"
        rf"{en_purpose},?{en_entry}|{en_entry},?{en_purpose})[。.]?")

    for start in range(len(rows)):
        for end in range(start + 1, min(start + 4, len(rows)) + 1):
            candidate = rows[start:end]
            if not heading.fullmatch("".join(value for _, value in candidate)):
                continue
            if size is None:
                return True
            boxes = [box(line) for line, _ in candidate]
            if any(bounds is None for bounds in boxes):
                continue
            left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
            right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
            # Position can change across iOS/layout updates. Require visible,
            # adjacent heading lines rather than a fixed part of the screen.
            if any(b[1] > a[3] + 3 * max(a[3] - a[1], b[3] - b[1])
                   or b[3] < a[1] for a, b in zip(boxes, boxes[1:])):
                continue
            # iOS 27 uses an unlabelled close icon and this subtitle instead
            # of a textual Cancel button. It sits directly below the heading.
            if not cancels and not any(
                    0 <= box(line)[1] - bottom <= 3 * (box(line)[3] - box(line)[1])
                    and max(left, box(line)[0]) < min(right, box(line)[2])
                    for line in subtitles):
                continue
            return True
    return False


class _AutoEntry:
    """Shared single-attempt grant consumption and ordered secret input."""

    def __init__(self, session):
        self.session = session
        self.sending = False
        self._active = False
        self._grant = None
        self._consumed = None
        self._completed = False
        self._input_generation = None

    def _allowed(self) -> bool:
        expected = self._consumed or self._grant
        unchanged = (self._completed or self._input_generation is None or self._input_generation ==
                     getattr(self.session, "passcode_input_generation", 0))
        return bool(self._active and self.session.ready and unchanged
                    and expected and _read_auto() == expected)

    def _consume(self) -> list:
        code = _read()
        try:
            if not code or not _VALID.fullmatch(code):
                raise PasscodeError("保存済みパスコードを確認し、設定し直してください")
            prepared = strokes(code)
            consumed = {**self._grant, "blocked": True}
            # Persist BEFORE the first key. A crash cannot cause a replay.
            _write_credential(AUTO_TARGET, json.dumps(consumed))
            self._consumed = consumed
            self.sending = True
            self.session._set(passcode_state="entering")
            return prepared
        finally:
            code = None

    def _command(self) -> dict:
        # Indigo sends keys directly. Registering a separate keyboard can run
        # the phone's keyboard-connect automation over the authentication UI.
        return {"op": "secret_strokes", "prepare": self._prepare, "guard": self._allowed,
                "register_keyboard": False}

    async def _send(self) -> None:
        global _entering
        if _entering or not self._allowed():
            raise PasscodeError("別のパスコード入力または設定変更のため停止しました")
        _entering = True
        try:
            command = self._command()
            pending = self.session.submit(command)
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Finish only the in-flight key-up before reporting stop.
                self._active = False
                if not command.get("_executing"):
                    pending.cancel()
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not pending.cancelled():
                    pending.exception()
                raise
            self._completed = True
        finally:
            self.sending = False
            _entering = False
            if self._consumed:
                self.session._set(passcode_state="blocked")


class _ManualEntry(_AutoEntry):
    """One GUI request, with secret retrieval delayed until the input queue."""

    def __init__(self, session):
        super().__init__(session)
        self._active = True
        self._video_paused = getattr(session.info, "video_paused", None)
        self._input_generation = getattr(session, "passcode_input_generation", 0)
        self._source_grant = None
        self._grant_checked = False

    def _allowed(self) -> bool:
        if (not self._active or not self.session.ready
                or self._input_generation != getattr(self.session, "passcode_input_generation", 0)
                or getattr(self.session.info, "video_paused", None) != self._video_paused):
            return False
        return not self._grant_checked or _read_auto() == (self._consumed or self._source_grant)

    async def _prepare(self) -> list:
        if not self._allowed():
            raise PasscodeError("入力または映像・通話の状態が変わったため停止しました")
        self._source_grant = _read_auto()
        self._grant_checked = True
        if not await shows_prompt(self.session):
            raise PasscodeError("XCTest のパスコード入力画面を確認できないため、入力しません")
        if not self._allowed():
            raise PasscodeError("入力または映像・通話の状態が変わったため停止しました")
        code = _read()
        try:
            if not code or not _VALID.fullmatch(code):
                raise PasscodeError("保存済みパスコードを確認してください")
            prepared = strokes(code)
            if self._source_grant and self._source_grant["udid"] == self.session.info.udid:
                # Invalidate any prior helper's confirmation as well as blocking
                # a new automatic attempt after this explicit manual input.
                consumed = {**self._source_grant, "blocked": True, "token": uuid.uuid4().hex}
                _write_credential(AUTO_TARGET, json.dumps(consumed))
                self._consumed = consumed
            self.sending = True
            self.session._set(passcode_state="entering")
            return prepared
        finally:
            code = None


class XCTestAutoEntry(_AutoEntry):
    """One startup's AX observations; no image capture or OCR fallback.

    Each scan advances on accessibility responses. Only a real XCTest startup
    event begins observation; WdaAgent cancels it on ready/exit/timeout.
    """

    def __init__(self, session):
        super().__init__(session)
        self._startup_events = []
        self._startup_seen = set()
        self._sub = None
        self._closed = False

    def notify_startup_event(self, kind: str) -> None:
        if (self._closed or kind in self._startup_seen
                or kind not in {"bundle-ready", "bundle-protocol", "bundle-capabilities",
                                "ui-initializing", "test-plan-started"}):
            return
        self._startup_seen.add(kind)
        self._startup_events.append(kind)
        if self._sub is not None and self._sub.queue.empty():
            self._sub.queue.put_nowait(("startup", None))

    async def _prepare(self) -> list:
        if not self._allowed():
            raise PasscodeError("自動入力が停止されたため、入力しませんでした")
        if not await shows_prompt(self.session) or not self._allowed():
            raise PasscodeError("XCTest の入力画面が変わったため、入力しませんでした")
        return self._consume()

    async def watch(self) -> None:
        sub = None
        try:
            self._grant = _read_auto()
            if not self._grant or self._grant["udid"] != self.session.info.udid:
                return
            if self._grant["blocked"]:
                self.session._set(passcode_state="blocked")
                return
            # AX does not depend on the video stream. "off" is the normal
            # state with video_on_connect disabled, not protected content.
            if self.session.info.video_paused not in (None, "call", "off", "camera"):
                self.session._set(passcode_state="stopped")
                return
            self._active = True
            self.session._set(passcode_state="watching")
            log.info("passcode: waiting for XCTest startup to observe accessibility")
            sub = self._sub = self.session.hub.subscribe()
            while not sub.queue.empty():
                sub.queue.get_nowait()
            while self._allowed():
                if not self._startup_seen:
                    kind, _ = await sub.queue.get()
                    if kind == "end":
                        return
                    if not self._startup_seen:
                        continue
                while not sub.queue.empty():
                    kind, _ = sub.queue.get_nowait()
                    if kind == "end":
                        return
                if not self._allowed():
                    return
                generation = getattr(self.session, "passcode_input_generation", 0)
                recognized = await shows_prompt(self.session)
                if recognized:
                    log.info("passcode: XCTest request identified through accessibility")
                if recognized and generation == getattr(self.session, "passcode_input_generation", 0):
                    self._input_generation = generation
                    if not self._allowed() or _entering:
                        return
                    await self._send()
                    log.info("passcode: XCTest entry sent once; awaiting WDA readiness")
                    return
                # Yield after a completed scan; no periodic timer or exception
                # retry. A later UI can appear after the initial DTX event.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.info("passcode: accessibility entry stopped (%s)", type(error).__name__)
            raise PasscodeError("XCTest の自動入力を停止しました。アクセシビリティで入力画面を確認できませんでした") from None
        finally:
            self._closed = True
            self._sub = None
            self._startup_events.clear()
            self._active = False
            self.sending = False
            if sub is not None:
                self.session.hub.unsubscribe(sub)
            if self._consumed:
                self.session._set(passcode_state="blocked")
            elif self._grant and self.session.info.passcode_state == "watching":
                self.session._set(passcode_state="stopped")

    def confirmed(self) -> None:
        if self._completed and self._consumed and _read_auto() == self._consumed:
            _write_credential(AUTO_TARGET, json.dumps(self._grant))
            self.session._set(passcode_state="confirmed")
            log.info("passcode: WDA startup confirmed; automatic entry rearmed")
