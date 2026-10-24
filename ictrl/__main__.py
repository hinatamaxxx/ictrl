"""iCtrl command line.

    python -m ictrl serve      run the local server (GUI + API) in the foreground
    python -m ictrl open       start the server if needed and open the app window
    python -m ictrl mcp        MCP server over stdio (Claude Code / Codex)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import subprocess
import sys
import time
import urllib.request
import json
from pathlib import Path

from . import DEFAULT_HOST, DEFAULT_PORT
from .manager import DATA_DIR
from .paths import LOG_DIR


def base_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}"


def ping(port: int, timeout: float = 0.5) -> bool:
    try:
        from . import pipe
        status, _, body = pipe.request("GET", "/api/ping", timeout=timeout + 2)
        return status == 200 and json.loads(body).get("app") == "iCtrl"
    except Exception:
        pass
    try:
        with urllib.request.urlopen(base_url(port) + "/api/ping", timeout=timeout) as r:
            return json.loads(r.read()).get("app") == "iCtrl"
    except Exception:
        return False


def ensure_server(port: int) -> None:
    """Start the background server once; wait until it listens."""
    if ping(port):
        return
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else python)
    root = Path(__file__).resolve().parent.parent
    from .paths import IN_PACKAGE
    if IN_PACKAGE:
        # Started by a packaged app (e.g. Codex): a child would run in its
        # container, with AppData writes redirected into that app's folder
        # (settings, logs and pairing records would split in two). Let the
        # Windows shell start the server instead, outside the container.
        # The shortcut lives in the program folder, which is not redirected.
        import win32com.client
        link = root / ".venv" / "iCtrl-server.lnk"
        shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(link))
        shortcut.TargetPath = exe
        shortcut.Arguments = f"-m ictrl serve --port {port}"
        shortcut.WorkingDirectory = str(root)
        shortcut.Save()
        subprocess.Popen(["explorer.exe", str(link)], close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        subprocess.Popen([exe, "-m", "ictrl", "serve", "--port", str(port)],
                         cwd=str(root), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:   # waits for our own process to bind, not for the phone
        if ping(port, 0.3):
            return
        time.sleep(0.15)
    raise SystemExit("iCtrl サーバーを起動できませんでした。ログ: " + str(LOG_DIR))


def open_window(port: int) -> None:
    """Open the app window: the GUI in WebView2 (ictrl/window.py)."""
    ensure_server(port)
    from . import window
    try:
        from .icon import ensure_ico
        icon = str(ensure_ico())
    except Exception:
        icon = None
    window.open_window(base_url(port) + "/", icon)


def setup_logging(console: bool) -> None:
    logs = LOG_DIR
    logs.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        logs / "ictrl.log", maxBytes=4 << 20, backupCount=3, encoding="utf-8")]
    if console and sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def serve(port: int) -> None:
    import uvicorn
    from .server import create_app
    setup_logging(console=True)
    # The server runs under pythonw (no console): a fatal error (a crash in
    # native code, a stuck thread) would leave no trace. Keep one.
    import faulthandler
    crash_log = open(LOG_DIR / "crash.log", "a", encoding="utf-8")
    crash_log.write(f"--- server start {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()}\n")
    crash_log.flush()
    faulthandler.enable(crash_log)
    if ping(port):
        print(f"iCtrl は既に {base_url(port)} で動作しています")
        return
    uvicorn.run(create_app(port), host=DEFAULT_HOST, port=port, log_level="warning",
                ws_max_size=1 << 20, log_config=None)


def stop_hook() -> None:
    """Claude Code Stop hook: when a Claude response that used iCtrl's tools
    ends, end UI automation (WebDriverAgent and the "AI が操作中" badge).
    Sessions that never touched iCtrl leave it alone (its tools are named
    mcp__ictrl__ictrl_*, or mcp__plugin_ictrl_ictrl__ictrl_* from the plugin).
    Never fails the hook."""
    import json
    import sys
    try:
        event = json.loads(sys.stdin.read() or "{}")
        transcript = event.get("transcript_path")
        if not transcript or "ictrl__ictrl_" not in open(transcript, encoding="utf-8", errors="ignore").read():
            return
        from . import pipe
        pipe.request("POST", "/api/automation/stop", {}, timeout=15)
    except Exception:
        pass


def install_claude_hook(settings_path=None) -> str:
    """Register `ictrl stop-hook` as a Claude Code Stop hook in the user's
    settings (~/.claude/settings.json), keeping everything already there."""
    import json
    import sys
    from pathlib import Path
    path = Path(settings_path or Path.home() / ".claude" / "settings.json")
    settings = json.loads(path.read_text("utf-8")) if path.exists() else {}
    command = f'"{Path(sys.executable).as_posix()}" -m ictrl stop-hook'
    stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
    if any("ictrl stop-hook" in str(h.get("command", "")) for e in stop for h in e.get("hooks", [])):
        return "Claude Code: 応答終了時の自動停止フックは登録済みです"
    stop.append({"hooks": [{"type": "command", "command": command, "timeout": 20}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + chr(10), "utf-8")
    return f"OK: Claude Code の応答が終わったら iCtrl の自動操作を止めるフックを {path} に登録しました"


def remove_claude_hook(settings_path=None) -> str:
    """Remove the Stop hook install_claude_hook added (uninstall)."""
    import json
    from pathlib import Path
    path = Path(settings_path or Path.home() / ".claude" / "settings.json")
    if not path.exists():
        return "Claude Code: 設定ファイルがありません"
    settings = json.loads(path.read_text("utf-8"))
    stop = settings.get("hooks", {}).get("Stop", [])
    kept = []
    for entry in stop:
        hooks = [h for h in entry.get("hooks", []) if "ictrl stop-hook" not in str(h.get("command", ""))]
        if hooks:
            kept.append({**entry, "hooks": hooks})
    if kept == stop:
        return "Claude Code: 応答終了時のフックは登録されていません"
    if kept:
        settings["hooks"]["Stop"] = kept
    else:
        settings["hooks"].pop("Stop", None)
        if not settings["hooks"]:
            settings.pop("hooks")
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + chr(10), "utf-8")
    return f"OK: {path} から iCtrl の応答終了時のフックを削除しました"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="ictrl")
    parser.add_argument("command", nargs="?", default="open", choices=["serve", "open", "mcp", "url", "wda-ipa", "stop-hook", "claude-hook", "claude-unhook"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("ICTRL_PORT", DEFAULT_PORT)))
    parser.add_argument("--bundle-id", default="com.ictrl.wda",
                        help="wda-ipa: bundle ID (an existing App ID reuses its slot)")
    parser.add_argument("--zip", help="wda-ipa: a downloaded WebDriverAgentRunner-Runner.zip")
    parser.add_argument("--out", default="WebDriverAgent-unsigned.ipa", help="wda-ipa: output file")
    args = parser.parse_args(argv)
    if args.command == "wda-ipa":
        from .wda import build_ipa
        info = build_ipa(args.out, args.bundle_id, args.zip)
        print(f"{info['ipa']} を作成しました（bundle ID: {info['bundle_id']}、"
              f"元: {info.get('release') or info.get('zip')}）。AltStore などで署名して iPhone に入れてください。")
        return
    if args.command == "claude-unhook":
        print(remove_claude_hook())
        return
    if args.command == "claude-hook":
        print(install_claude_hook())
        return
    if args.command == "stop-hook":
        stop_hook()
        return
    if args.command == "serve":
        serve(args.port)
    elif args.command == "open":
        open_window(args.port)
    elif args.command == "url":
        ensure_server(args.port)
        print(base_url(args.port) + "/")
    else:
        from .mcp import run
        run(args.port)


if __name__ == "__main__":
    main()
