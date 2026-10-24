"""Save iPhone screenshots: by default in an "iCtrl" folder inside the
user's Pictures known folder (wherever it is: OneDrive, another drive); the
user may choose another folder in the iCtrl window (setting screenshot_dir)."""
from __future__ import annotations

import ctypes
import os
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Optional

FOLDERID_PICTURES = uuid.UUID("33e28130-4e1e-4676-835a-98395c3bc3bb")
FOLDERID_DESKTOP = uuid.UUID("b4bfcc3a-db2c-424c-b029-7fe99a87c641")
KF_FLAG_CREATE = 0x00008000

last_saved: Optional[Path] = None
custom_dir: Optional[str] = None     # the screenshot_dir setting, set by the manager


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]


def _known_folder(folder_id: uuid.UUID) -> Optional[Path]:
    guid = _GUID()
    ctypes.memmove(guid.data, folder_id.bytes_le, 16)
    out = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), KF_FLAG_CREATE, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ctypes.WinDLL("ole32").CoTaskMemFree(out)


def default_folder() -> Path:
    return (_known_folder(FOLDERID_PICTURES) or Path.home() / "Pictures") / "iCtrl"


def desktop() -> Path:
    """The user's desktop, wherever it was moved (OneDrive and the like)."""
    return _known_folder(FOLDERID_DESKTOP) or Path.home() / "Desktop"


def folder() -> Path:
    path = Path(custom_dir) if custom_dir else default_folder()
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_dir(value) -> Optional[str]:
    """The screenshot_dir setting: None (the default) or an absolute folder."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("保存先はフォルダーの完全なパスで指定してください")
    path = Path(value)
    if path.exists() and not path.is_dir():
        raise ValueError("保存先がフォルダーではありません")
    return str(path)


def save(png: bytes, now: Optional[float] = None) -> Path:
    """Writes the PNG as "iPhone 2026-10-21 163512.png" (a suffix if taken)."""
    global last_saved
    directory = folder()
    stem = "iPhone " + time.strftime("%Y-%m-%d %H%M%S", time.localtime(now))
    path = directory / f"{stem}.png"
    n = 1
    while path.exists():
        n += 1
        path = directory / f"{stem} ({n}).png"
    path.write_bytes(png)
    last_saved = path
    return path


def as_viewed(png: bytes, rotation: int = 0) -> bytes:
    """The screenshot in the orientation the iCtrl window shows the phone:
    upright (portrait) or turned by `rotation` degrees clockwise, as the
    iOS (or an agent's orientation) turned it. The phone's
    own image does not always match: with the display off it came landscape
    (2622x1206). An image already in the shown shape is kept as
    is; otherwise it is turned the way the window turns the phone."""
    import io
    from PIL import Image
    image = Image.open(io.BytesIO(png))
    want_landscape = rotation % 180 != 0
    if (image.width > image.height) == want_landscape:
        return png
    image = image.rotate(-(rotation or 90), expand=True)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


SHOWN_TIMEOUT_MS = 5000


def reveal(owner: int = 0) -> None:
    """Shows the last saved screenshot in Explorer, selected (or the folder).
    Explorer does the work, as browsers' "Show in folder" does: it reuses a
    window already showing that folder and selects the file. Only bringing
    that window to the front is left to iCtrl (see bring_forward)."""
    target = last_saved if last_saved is not None and last_saved.exists() else None
    if target is None:
        os.startfile(folder())
        return
    import pythoncom
    import win32com.client
    import win32event
    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        before = {int(w.HWND) for w in shell.Windows()}
        _open_and_select(target)
        # The call returns once Explorer has registered its window.
        windows = {int(w.HWND): w for w in shell.Windows()}
        new = [hwnd for hwnd in windows if hwnd not in before]
        if len(new) == 1:
            # A new window is shown a little later (0.13 s registered, 0.7 s
            # showing the folder, measured); raised before that, Explorer's
            # own showing puts it back behind. Wait for its navigation event.
            hwnd, window = new[0], windows[new[0]]
            shown = win32event.CreateEvent(None, False, False, None)

            class Shown:
                def OnNavigateComplete2(self, *_args):
                    win32event.SetEvent(shown)

            hook = win32com.client.DispatchWithEvents(window, Shown)
            deadline = time.monotonic() + SHOWN_TIMEOUT_MS / 1000
            while not _shows(window, target.parent):
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    break
                win32event.MsgWaitForMultipleObjects([shown], False, remaining, win32event.QS_ALLINPUT)
                pythoncom.PumpWaitingMessages()
            del hook
        else:
            hwnd = next((h for h, w in windows.items() if _shows(w, target.parent)), 0)
        bring_forward(owner, hwnd)
    finally:
        pythoncom.CoUninitialize()


def _open_and_select(target: Path) -> None:
    shell32 = ctypes.WinDLL("shell32")
    pidl = ctypes.c_void_p()
    if shell32.SHParseDisplayName(ctypes.c_wchar_p(str(target)), None, ctypes.byref(pidl), 0, None) != 0:
        raise OSError(f"{target.name} が見つかりません")
    try:
        # cidl 0: open the file's folder with the file selected.
        hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
        if hr != 0:
            raise OSError(f"エクスプローラーで開けませんでした ({hr & 0xFFFFFFFF:#x})")
    finally:
        ctypes.WinDLL("ole32").CoTaskMemFree(pidl)


def _shows(window, directory: Path) -> bool:
    try:
        return window.Document.Folder.Self.Path.casefold() == str(directory).casefold()
    except Exception:           # a window still navigating, or a non-folder view
        return False


def bring_forward(owner: int, hwnd: int = 0) -> None:
    """The server runs in the background, and Windows keeps what it opens
    behind the window in front; it also refuses to let a background process
    hand that right to Explorer (AllowSetForegroundWindow: access denied).
    Joined for a moment to the input of the iCtrl window the
    user just clicked, the server may set the foreground: to the given
    window, or to the owner so that a dialog it opens next comes forward."""
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    # The window in front now (normally still the owner the user clicked).
    front = user32.GetForegroundWindow() or owner
    if not front:
        return
    theirs = user32.GetWindowThreadProcessId(front, None)
    ours = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    attached = bool(theirs) and theirs != ours and bool(user32.AttachThreadInput(ours, theirs, True))
    try:
        target = hwnd or owner
        if hwnd and user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)              # SW_RESTORE
        user32.BringWindowToTop(target)
        user32.SetForegroundWindow(target)
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)


# IFileOpenDialog in folder mode (pywin32 does not wrap it), called through
# its COM vtable. Indices: IUnknown 0-2, IModalWindow.Show 3, IFileDialog
# SetOptions 9, GetOptions 10, SetFolder 12, SetTitle 17, SetOkButtonLabel 18,
# GetResult 20; IShellItem.GetDisplayName 5.
CLSID_FILE_OPEN_DIALOG = uuid.UUID("dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")
IID_FILE_OPEN_DIALOG = uuid.UUID("d57c7288-d4ad-4768-be02-9d969532d960")
IID_SHELL_ITEM = uuid.UUID("43826d1e-e718-42ee-bc55-a1e261c37bfe")
FOS_PICKFOLDERS = 0x20
FOS_FORCEFILESYSTEM = 0x40
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED = 0x800704C7


def _guid(value: uuid.UUID) -> _GUID:
    guid = _GUID()
    ctypes.memmove(guid.data, value.bytes_le, 16)
    return guid


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)


def pick_folder(owner: int, start: Path) -> Optional[Path]:
    """Windows' folder picker, owned by (and in front of) the iCtrl window.
    Returns None when the user cancels. Runs on its own thread."""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, 0x2)            # COINIT_APARTMENTTHREADED
    dialog, start_item, result = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        hr = ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_FILE_OPEN_DIALOG)), None, 1,  # CLSCTX_INPROC_SERVER
                                    ctypes.byref(_guid(IID_FILE_OPEN_DIALOG)), ctypes.byref(dialog))
        if hr != 0:
            raise OSError(f"フォルダーの選択画面を開けませんでした ({hr & 0xFFFFFFFF:#x})")
        options = wintypes.DWORD()
        _method(dialog, 10, ctypes.POINTER(wintypes.DWORD))(dialog, ctypes.byref(options))
        _method(dialog, 9, wintypes.DWORD)(dialog, options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
        _method(dialog, 17, ctypes.c_wchar_p)(dialog, "iPhone のスクリーンショットの保存先")
        _method(dialog, 18, ctypes.c_wchar_p)(dialog, "このフォルダーに保存")
        if start.is_dir() and ctypes.WinDLL("shell32").SHCreateItemFromParsingName(
                ctypes.c_wchar_p(str(start)), None, ctypes.byref(_guid(IID_SHELL_ITEM)),
                ctypes.byref(start_item)) == 0:
            _method(dialog, 12, ctypes.c_void_p)(dialog, start_item)
        bring_forward(owner)
        hr = _method(dialog, 3, wintypes.HWND)(dialog, owner or None)
        if hr & 0xFFFFFFFF == ERROR_CANCELLED:
            return None
        if hr != 0:
            raise OSError(f"フォルダーを選べませんでした ({hr & 0xFFFFFFFF:#x})")
        _method(dialog, 20, ctypes.POINTER(ctypes.c_void_p))(dialog, ctypes.byref(result))
        name = ctypes.c_wchar_p()
        if _method(result, 5, ctypes.c_uint, ctypes.POINTER(ctypes.c_wchar_p))(
                result, SIGDN_FILESYSPATH, ctypes.byref(name)) != 0:
            raise OSError("選んだ場所はフォルダーとして使えません")
        try:
            return Path(name.value)
        finally:
            ole32.CoTaskMemFree(name)
    finally:
        for obj in (result, start_item, dialog):
            _release(obj)
        ole32.CoUninitialize()


def foreground_window() -> int:
    """The window the user is using now (iCtrl's, when they clicked in it)."""
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    return user32.GetForegroundWindow() or 0
