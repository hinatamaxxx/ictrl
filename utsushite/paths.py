"""Where utsushite keeps its files (Windows conventions).

- settings and the device list:  %APPDATA%\\utsushite          (CONFIG_DIR)
- logs, pairing records, pymobiledevice3's data, the window's browser
  profile and caches:            %LOCALAPPDATA%\\utsushite     (LOCAL_DIR)
  Pairing records are keys bound to this PC, so they stay out of the
  roaming profile.

UTSUSHITE_HOME puts everything in one folder instead (tests, portable use).
Earlier versions kept everything in ~/.ictrl, and then (while utsushite was
called iCtrl) in %APPDATA%\\iCtrl and %LOCALAPPDATA%\\iCtrl; both are moved
over once.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_home = os.environ.get("UTSUSHITE_HOME")
if _home:
    CONFIG_DIR = LOCAL_DIR = Path(_home)
else:
    CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "utsushite"
    LOCAL_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "utsushite"
# utsushite was called iCtrl until 2026-10-25: its folders are moved over once.
RENAMED_FROM = ((CONFIG_DIR.with_name("iCtrl"), CONFIG_DIR), (LOCAL_DIR.with_name("iCtrl"), LOCAL_DIR))

LOG_DIR = LOCAL_DIR / "logs"


def in_package() -> bool:
    """Whether this process runs inside a packaged (MSIX) app's container,
    e.g. started by Codex. Its writes to AppData are redirected into that
    app's private folder, so it must not keep utsushite's data or start the
    server itself (see __main__.ensure_server)."""
    if os.name != "nt":
        return False
    import ctypes
    length = ctypes.c_uint32(0)
    try:
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
    except (AttributeError, OSError):
        return False
    return result != 15700          # APPMODEL_ERROR_NO_PACKAGE


IN_PACKAGE = in_package()
LEGACY_DIR = Path.home() / ".ictrl"
_CONFIG_FILES = ("settings.json", "devices.json")
_LOCAL_ITEMS = ("logs", "pairing", "pymobiledevice3", "audio_sample.bin",
                "window-profile", "window-profile-chrome", "window-profile-msedge")


MARKER = ".migrated-from-dot-ictrl"


def _move(src: Path, dst: Path, left: list, name: str) -> None:
    """Move src to dst. Anything already at dst was made by a newer run
    before the move (e.g. a fresh pairing identity while the old server
    held the files): the earlier version's data wins."""
    try:
        if dst.is_dir() and src.is_dir():
            for child in src.iterdir():
                _move(child, dst / child.name, left, f"{name}/{child.name}")
            src.rmdir()
            return
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except OSError:
        left.append(name)


def migrate_legacy() -> list[str]:
    """Move ~/.utsushite into the new folders, once (a marker records it). An
    item that cannot be moved (in use) stays and is reported; folders utsushite
    does not know are left in ~/.utsushite. Returns what was not moved."""
    if _home or not LEGACY_DIR.is_dir():
        return []
    left: list[str] = []
    for name in (*_CONFIG_FILES, *_LOCAL_ITEMS):
        src = LEGACY_DIR / name
        if src.exists():
            _move(src, (CONFIG_DIR if name in _CONFIG_FILES else LOCAL_DIR) / name, left, name)
    if not left:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        (LOCAL_DIR / MARKER).write_text("moved from " + str(LEGACY_DIR), "utf-8")
    try:
        LEGACY_DIR.rmdir()          # only when nothing else is left in it
    except OSError:
        pass
    return left


def migrate_renamed() -> list[str]:
    """Move the iCtrl folders (the earlier name) into utsushite's. What
    cannot be moved (in use) stays and is reported."""
    if _home:
        return []
    left: list[str] = []
    for old, new in RENAMED_FROM:
        if old.is_dir():
            _move(old, new, left, old.name)
    return left


def _server_running(port: int = 47800) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(os.environ.get("UTSUSHITE_PORT", port))), 0.3):
            return True
    except OSError:
        return False


# Once, before anything creates the new folders, and only while no utsushite
# server is running (a running one holds its log and profile files open).
import sys as _sys

if (not _home and "unittest" not in _sys.modules and LEGACY_DIR.is_dir()
        and not IN_PACKAGE and not (LOCAL_DIR / MARKER).exists()
        and any((LEGACY_DIR / n).exists() for n in (*_CONFIG_FILES, *_LOCAL_ITEMS))
        and not _server_running()):
    MIGRATION_LEFT = migrate_legacy()
else:
    MIGRATION_LEFT = []

# The same for the folders of iCtrl (utsushite's earlier name).
if (not _home and "unittest" not in _sys.modules and not IN_PACKAGE
        and any(old.is_dir() for old, _new in RENAMED_FROM) and not _server_running()):
    MIGRATION_LEFT += migrate_renamed()
