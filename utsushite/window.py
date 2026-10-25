"""utsushite's app window (window/UtsushiteWindow.cs): the GUI in WebView2, the Edge
engine that comes with Windows and updates itself, with the browser features
turned off (as the AdGuard Home tray app does).

The window is compiled on first use with the C# compiler that is part of the
.NET Framework in every Windows 10/11, so users need no build tools. The
WebView2 SDK DLLs (Microsoft, redistributable) are in window/lib.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .paths import LOCAL_DIR

SOURCE_DIR = Path(__file__).resolve().parent.parent / "window"
OUT_DIR = LOCAL_DIR / "window"
EXE = OUT_DIR / "UtsushiteWindow.exe"
DATA_DIR = LOCAL_DIR / "WebView2"
_DLLS = ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")


def csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


def _inputs() -> list[Path]:
    return [SOURCE_DIR / "UtsushiteWindow.cs", SOURCE_DIR / "app.manifest"] + [SOURCE_DIR / "lib" / d for d in _DLLS]


def is_current() -> bool:
    if not EXE.exists():
        return False
    built = EXE.stat().st_mtime
    return all(p.stat().st_mtime <= built for p in _inputs()) and all((OUT_DIR / d).exists() for d in _DLLS)


def build() -> Path:
    """Compile the window (about a second) when the source is newer."""
    if is_current():
        return EXE
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lib = SOURCE_DIR / "lib"
    for d in _DLLS:
        shutil.copy2(lib / d, OUT_DIR / d)
    tmp = EXE.with_suffix(".new.exe")
    cmd = [str(csc()), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
           f"/out:{tmp}", f"/win32manifest:{SOURCE_DIR / 'app.manifest'}",
           "/r:System.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
           f"/r:{lib / _DLLS[0]}", f"/r:{lib / _DLLS[1]}",
           str(SOURCE_DIR / "UtsushiteWindow.cs")]
    done = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
    if done.returncode != 0:
        raise RuntimeError("utsushite のウィンドウをビルドできませんでした: " + (done.stdout or done.stderr).strip())
    _put(tmp, EXE)
    return EXE


def _put(new: Path, exe: Path) -> None:
    # A running exe stays open: move it aside, then put the new one.
    if exe.exists():
        old = exe.with_suffix(".old.exe")
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            exe.replace(old)
        except OSError:
            pass
    new.replace(exe)


def open_window(url: str, icon: str | None = None) -> None:
    exe = build()
    args = [str(exe), "--url", url, "--data", str(DATA_DIR)]
    if icon:
        args += ["--icon", icon]
    subprocess.Popen(args, creationflags=0x00000008, close_fds=True)
