"""Read the Windows IME state (日本語入力 on/off) of the foreground window.

The iPhone follows the Windows IME as the taskbar shows it (あ / A): the IME
of the window that has the keyboard focus, whatever app it belongs to. It is
read when something may have changed it (imewatch.py), not polled. Uses the documented WM_IME_CONTROL queries on the default
IME window, which also works for TSF-based IMEs.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

WM_IME_CONTROL = 0x0283
IMC_GETCONVERSIONMODE = 0x0001
IMC_GETOPENSTATUS = 0x0005
IME_CMODE_NATIVE = 0x0001       # Japanese (hiragana/katakana) vs alphanumeric
IME_CMODE_KATAKANA = 0x0002
IME_CMODE_FULLSHAPE = 0x0008
SMTO_ABORTIFHUNG = 0x0002


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


def _focus_window(user32) -> int:
    """The window that has the keyboard focus in the foreground app. In the
    iCtrl window that is the WebView2 page, a child window of another
    process (msedgewebview2) with its own IME context, not the top level."""
    user32.GetForegroundWindow.restype = wintypes.HWND
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if user32.GetGUIThreadInfo(0, ctypes.byref(info)) and info.hwndFocus:
        return info.hwndFocus
    return user32.GetForegroundWindow()


def state() -> dict:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
    imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    hwnd = _focus_window(user32)
    if not hwnd:
        return {"available": False}
    ime = imm32.ImmGetDefaultIMEWnd(hwnd)
    if not ime:
        return {"available": False}

    def ask(cmd: int):
        result = ctypes.c_size_t()
        ok = user32.SendMessageTimeoutW(ime, WM_IME_CONTROL, cmd, 0, SMTO_ABORTIFHUNG, 200,
                                        ctypes.byref(result))
        return result.value if ok else None

    open_status = ask(IMC_GETOPENSTATUS)
    mode = ask(IMC_GETCONVERSIONMODE)
    if open_status is None or mode is None:
        return {"available": False}
    japanese = bool(open_status) and bool(mode & IME_CMODE_NATIVE)
    return {"available": True, "open": bool(open_status), "conversion_mode": int(mode),
            "japanese": japanese, "katakana": bool(mode & IME_CMODE_KATAKANA),
            "fullwidth": bool(mode & IME_CMODE_FULLSHAPE)}


IMC_SETCONVERSIONMODE = 0x0002
IMC_SETOPENSTATUS = 0x0006


def set_japanese(japanese: bool) -> dict:
    """Turn the foreground window's IME on (ひらがな) or off (A)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
    imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    ime = imm32.ImmGetDefaultIMEWnd(_focus_window(user32))
    if not ime:
        return {"available": False}
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(ime, WM_IME_CONTROL, IMC_SETOPENSTATUS, 1 if japanese else 0,
                               SMTO_ABORTIFHUNG, 200, ctypes.byref(result))
    if japanese:
        current = state()
        mode = current.get("conversion_mode", 0) if current.get("available") else 0
        if not mode & IME_CMODE_NATIVE:
            user32.SendMessageTimeoutW(ime, WM_IME_CONTROL, IMC_SETCONVERSIONMODE,
                                       (mode | IME_CMODE_NATIVE | IME_CMODE_FULLSHAPE) & ~IME_CMODE_KATAKANA,
                                       SMTO_ABORTIFHUNG, 200, ctypes.byref(result))
    return state()
