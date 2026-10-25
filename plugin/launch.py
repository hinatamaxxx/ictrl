"""Start utsushite from the Claude Code plugin (or any MCP client) with uv.
plugin/utsushite.cmd finds or fetches uv and runs this:

    uv run --no-project --managed-python --python 3.14 plugin/launch.py mcp

The first run prepares the program folder's .venv, the same layout setup.ps1
makes: uv provides Python (its own build, checked against hashes built into
uv) and installs requirements.txt exactly as pinned, every file checked
against its hash (--require-hashes), binaries only, and pymobiledevice3
without its own dependency list (--no-deps; see requirements.txt). Nothing
outside this folder and uv's cache is written; removing the plugin removes it.

Standard library only: this runs before utsushite's packages exist. Progress goes
to stderr, because stdout is the MCP connection.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe"
STAMP = VENV / "utsushite-requirements.sha256"
PYTHON_VERSION = "3.14"


def say(text: str) -> None:
    print(f"utsushite: {text}", file=sys.stderr, flush=True)


def wanted() -> str:
    """What the environment must match: the pinned list and the bundled wheels."""
    digest = hashlib.sha256((ROOT / "requirements.txt").read_bytes())
    for wheel in sorted((ROOT / "wheels").glob("*.whl")):
        digest.update(wheel.name.encode() + wheel.read_bytes())
    return digest.hexdigest()


def ready() -> bool:
    return PYTHON.exists() and STAMP.exists() and STAMP.read_text().strip() == wanted()


def in_package() -> bool:
    """Whether this runs inside a packaged (MSIX) app, such as the Codex app.
    Its writes to AppData land in that app's private folder, so a Python
    installed there would not exist for the utsushite server, which starts outside
    the package (utsushite/__main__.py ensure_server)."""
    if os.name != "nt":
        return False
    import ctypes
    length = ctypes.c_uint32(0)
    try:
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
    except (AttributeError, OSError):
        return False
    return result != 15700   # APPMODEL_ERROR_NO_PACKAGE


def uv() -> str:
    found = os.environ.get("UV") or shutil.which("uv")   # uv run sets UV to itself
    if not found:
        raise SystemExit("utsushite: uv が見つかりません。次のコマンドで入れてから、もう一度試してください。\n"
                         "  winget install astral-sh.uv")
    return found


class _SetupLock:
    """One setup at a time, across processes (two sessions starting together).
    A named Windows mutex: waiting is the OS's, not a retry loop."""
    def __enter__(self):
        import ctypes
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.restype = ctypes.c_void_p
        name = "Local\\utsushite-setup-" + hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
        self.handle = self.kernel32.CreateMutexW(None, False, name)
        self.kernel32.WaitForSingleObject(ctypes.c_void_p(self.handle), 0xFFFFFFFF)
        return self

    def __exit__(self, *exc):
        import ctypes
        self.kernel32.ReleaseMutex(ctypes.c_void_p(self.handle))
        self.kernel32.CloseHandle(ctypes.c_void_p(self.handle))


def prepare() -> None:
    if ready():
        return
    with _SetupLock():
        if ready():             # the other process finished it
            return
        tool = uv()
        say("初回の準備をしています（Python と依存パッケージ、約 200MB。1 分ほどかかります）…")
        STAMP.unlink(missing_ok=True)
        if in_package():
            # The environment's Python goes into this folder, outside AppData.
            os.environ["UV_PYTHON_INSTALL_DIR"] = str(ROOT / ".uv" / "python")
        subprocess.run([tool, "venv", "--quiet", "--allow-existing", "--managed-python",
                        "--python", PYTHON_VERSION, str(VENV)], check=True, stdout=sys.stderr)
        subprocess.run([tool, "pip", "install", "--quiet", "--python", str(PYTHON),
                        "--no-deps", "--only-binary", ":all:", "--require-hashes",
                        "--find-links", str(ROOT / "wheels"),
                        "-r", str(ROOT / "requirements.txt")], check=True, stdout=sys.stderr)
        # utsushite itself runs from this folder, as setup.ps1's editable install does,
        # without building anything.
        site = Path(subprocess.run([str(PYTHON), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                                   check=True, capture_output=True, text=True).stdout.strip())
        (site / "utsushite.pth").write_text(str(ROOT) + "\n", encoding="utf-8")
        STAMP.write_text(wanted() + "\n")
        say("準備ができました")


def main(argv: list[str]) -> int:
    sys.stderr.reconfigure(encoding="utf-8")   # a pipe would get the console's code page
    try:
        prepare()
    except subprocess.CalledProcessError as error:
        say(f"準備に失敗しました（{Path(error.cmd[0]).name} {error.cmd[1]}、終了コード {error.returncode}）。"
            "ネットワークを確かめて、もう一度試してください")
        return 1
    return subprocess.run([str(PYTHON), "-m", "utsushite", *argv]).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
