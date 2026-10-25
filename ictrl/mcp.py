"""MCP (Model Context Protocol) server over stdio, for Claude Code and Codex.

It is a thin client of the local iCtrl server, so an agent and the GUI share
the same single device session: what the agent does is visible live in the
app window, and the user can take over at any time.
"""
from __future__ import annotations

import base64
import contextlib
import json
import sys
from typing import Any

from . import __version__
from .__main__ import base_url, ensure_server

PROTOCOL_VERSION = "2025-06-18"

COORDS = ("座標は直前の ictrl_screenshot 画像のピクセル座標で指定します"
          "（画像の幅・高さは自動で使われます）。0〜1 の正規化座標も可。")

TOOLS: list[dict[str, Any]] = [
    {"name": "ictrl_status", "description": "iPhone の接続状態（接続方式、端末名、iOS、画面サイズ、映像統計）を返す。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ictrl_devices", "description": "PC から見えている iPhone と使える接続方式（usb / wifi）を一覧する。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ictrl_connect", "description": ("iPhone に接続して画面ストリームと操作を開始する。既に接続済みなら何もしない。"
                                              "iCtrl の画面がどこにも開いていなければ、結果に画面の URL が付くので、"
                                              "自分のアプリ内ブラウザーで開いてユーザーに見せる。"),
     "inputSchema": {"type": "object", "properties": {
         "transport": {"type": "string", "enum": ["usb", "wifi"], "default": "usb",
                       "description": "usb か、PC と同じネットワークの wifi"},
         "udid": {"type": "string", "description": "省略時は最初に見つかった端末"}}}},
    {"name": "ictrl_disconnect", "description": "iPhone との接続を閉じる。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ictrl_finish", "description": ("頼まれた操作をすべて終えたら必ず呼ぶ。iPhone の UI 自動操作"
                                             "（WebDriverAgent と iPhone 上の Automation Running 表示）を終了する。"
                                             "iCtrl の画面が見えていなければ iPhone との接続も切る（使っていない間に"
                                             "通信を残さないため）。次に使うときは ictrl_connect からやり直す。"),
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ictrl_screenshot", "description": ("現在の iPhone 画面を画像で返す。画像はトークンを多く使い遅いので、WebDriverAgent で要素が読めている間は使わない。"
                                                "使うのは、ictrl_elements が OCR（source: ocr）になったとき、目的のボタンが一覧に無い（文字のないアイコン等）とき、"
                                                "写真・地図など画像の中身で判断が要るときだけ。"),
     "inputSchema": {"type": "object", "properties": {
         "max_size": {"type": "integer", "default": 1024, "description": "長辺の最大ピクセル数"}}}},
    {"name": "ictrl_elements", "description": ("画面上の要素（ボタン・アイコン・文字・入力欄など）の種類とラベルを、"
                                              "画像なしの一覧で返す（WebDriverAgent 使用、初回は起動に数秒）。"
                                              "操作前後の確認はこの一覧を優先し、ictrl_tap_element で名前を指定して押せる。"),
     "inputSchema": {"type": "object", "properties": {
         "filter": {"type": "string", "description": "ラベルに含まれる文字で絞り込む（任意）"}}}},
    {"name": "ictrl_tap_element", "description": ("ラベルが text に一致（なければ部分一致）する要素をタップする。"
                                                 "同名が複数あるときは index（0 から）で選ぶ。座標は不要。"
                                                 "texts に複数指定すると、画面を1回読んで順に押す（電卓のキーなど配置が変わらない所向け）。"),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "texts": {"type": "array", "items": {"type": "string"}},
         "index": {"type": "integer", "default": 0}}}},
    {"name": "ictrl_wait_for", "description": ("ラベル text の要素が画面に現れるまで（gone=true なら消えるまで）待つ。"
                                              "読み込みや画面遷移の完了待ちに使い、スクリーンショットを繰り返さなくてよい。"),
     "inputSchema": {"type": "object", "required": ["text"], "properties": {
         "text": {"type": "string"}, "gone": {"type": "boolean", "default": False},
         "timeout": {"type": "number", "default": 10, "description": "秒（最大 60）"}}}},
    {"name": "ictrl_scroll_to", "description": ("ラベル text の要素が画面に入るまでスクロールする（端に達したら止まる）。"
                                               "見つかった要素の座標を返すので、続けて ictrl_tap_element で押せる。"),
     "inputSchema": {"type": "object", "required": ["text"], "properties": {
         "text": {"type": "string"},
         "direction": {"type": "string", "enum": ["down", "up", "left", "right"], "default": "down",
                       "description": "down は下の内容を表示（指は下から上）"},
         "max": {"type": "integer", "default": 12, "description": "最大スワイプ回数"}}}},
    {"name": "ictrl_alert", "description": ("画面のアラート（権限の確認などシステムのものを含む）を読む、または押す。"
                                           "action を省略すると内容とボタン一覧を返す。accept / dismiss、name でボタン名指定。"),
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["accept", "dismiss"]},
         "name": {"type": "string", "description": "押すボタンのラベル（任意）"}}}},
    {"name": "ictrl_open_url", "description": "URL を開く（https:// は Safari、アプリの URL スキームやユニバーサルリンクも可）。",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}}},
    {"name": "ictrl_terminate_app", "description": "バンドルIDのアプリを終了する。",
     "inputSchema": {"type": "object", "required": ["bundle_id"], "properties": {"bundle_id": {"type": "string"}}}},
    {"name": "ictrl_pinch", "description": "二本指のピンチ（scale > 1 で拡大、< 1 で縮小）を画面中央で行う。",
     "inputSchema": {"type": "object", "required": ["scale"], "properties": {"scale": {"type": "number"}}}},
    {"name": "ictrl_rotate", "description": "二本指の回転ジェスチャー（度、時計回りが正）。",
     "inputSchema": {"type": "object", "required": ["degrees"], "properties": {"degrees": {"type": "number"}}}},
    {"name": "ictrl_orientation", "description": "画面の向きを読む、または変える（PORTRAIT / LANDSCAPE。アプリが対応している場合）。",
     "inputSchema": {"type": "object", "properties": {
         "set": {"type": "string", "enum": ["PORTRAIT", "LANDSCAPE"]}}}},
    {"name": "ictrl_screenshot_marks", "description": ("要素ごとに番号を描き込んだスクリーンショットと番号の一覧を返す。"
                                                      "ictrl_tap_mark で番号を指定して押せる（座標の推測が不要）。"),
     "inputSchema": {"type": "object", "properties": {
         "max_size": {"type": "integer", "default": 1024}}}},
    {"name": "ictrl_tap_mark", "description": "直前の ictrl_screenshot_marks の番号 n の要素をタップする。",
     "inputSchema": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}},
    {"name": "ictrl_tap", "description": "画面をタップする。" + COORDS,
     "inputSchema": {"type": "object", "required": ["x", "y"], "properties": {
         "x": {"type": "number"}, "y": {"type": "number"},
         "hold_ms": {"type": "integer", "default": 50, "description": "長押しは 600 以上"},
         "count": {"type": "integer", "default": 1, "description": "2 でダブルタップ"}}}},
    {"name": "ictrl_swipe", "description": "スワイプ／ドラッグ／スクロールする。上方向スクロールは指を下から上へ。" + COORDS,
     "inputSchema": {"type": "object", "required": ["x1", "y1", "x2", "y2"], "properties": {
         "x1": {"type": "number"}, "y1": {"type": "number"},
         "x2": {"type": "number"}, "y2": {"type": "number"},
         "duration_ms": {"type": "integer", "default": 300}}}},
    {"name": "ictrl_button", "description": "ハードウェアボタンを押す。",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string", "enum": ["home", "app-switcher", "lock", "volume-up",
                                              "volume-down", "mute", "siri"]}}}},
    {"name": "ictrl_type_text", "description": "フォーカス中の入力欄に文字を入力する（日本語可。英数字以外はクリップボード経由で貼り付け）。",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}},
    {"name": "ictrl_key", "description": "キーを押す（enter, backspace, tab, escape, up/down/left/right, a-z 等）。modifiers で cmd/shift/ctrl/alt 併用可。日本語/英字入力の切替は kana（日本語）/ eisu（英字）。",
     "inputSchema": {"type": "object", "required": ["key"], "properties": {
         "key": {"type": "string"},
         "modifiers": {"type": "array", "items": {"type": "string",
                                                  "enum": ["cmd", "shift", "ctrl", "alt"]}}}}},
    {"name": "ictrl_list_apps", "description": "インストール済みアプリ（名前とバンドルID）を一覧する。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "名前またはバンドルIDの部分一致で絞り込み。標準アプリは英語名（Settings, Safari, Camera など）"}}}},
    {"name": "ictrl_launch_app", "description": "バンドルIDでアプリを起動する（例: com.apple.Preferences）。",
     "inputSchema": {"type": "object", "required": ["bundle_id"], "properties": {
         "bundle_id": {"type": "string"}}}},
    {"name": "ictrl_clipboard", "description": "iPhone のクリップボードを読む（text 省略時。画像なら画像で返す）または text を書き込む。",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "ictrl_open_window", "description": ("iCtrl の画面の URL を返す。Claude Code や Codex から使うときは、アプリのウィンドウは開かず、"
                                                  "この URL を自分のアプリ内ブラウザー（Claude のブラウザー画面など）で開いてユーザーに見せる。"),
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ictrl_wda_ipa", "description": ("iPhone の補助アプリ（WebDriverAgent。ホーム画面の名前は「iCtrl WDA」）の未署名 IPA を、"
                                              "Appium の公式リリースから作ってデスクトップに保存する。署名と iPhone への導入は利用者が"
                                              "Sideloadly や AltStore で自分の Apple ID を使って行う（AI は行わない）。"),
     "inputSchema": {"type": "object", "properties": {
         "bundle_id": {"type": "string", "description": "省略時は com.ictrl.wda。無料の Apple ID で App ID が足りないときだけ、登録済みのアプリと同じ ID を指定する（そのアプリは置き換わる）"}}}},
]


class Client:
    def __init__(self, port: int):
        self.port = port
        self.url = base_url(port)
        self.shot_size: tuple[int, int] | None = None

    def call(self, method: str, path: str, body: dict | None = None, raw: bool = False, timeout=60):
        """Ask the iCtrl server over its named pipe (loopback TCP can be
        intercepted by local filters; see pipe.py)."""
        from . import pipe
        ensure_server(self.port)
        status, headers, data = pipe.request(method, path, body, timeout=timeout)
        if status >= 400:
            try:
                message = json.loads(data).get("error")
            except Exception:
                message = None
            raise RuntimeError(message or f"HTTP {status}")
        if raw:
            return data, {k.lower(): v for k, v in headers.items()}
        return json.loads(data)

    def coords(self, args: dict, *names: str) -> dict:
        out = {}
        for n in names:
            out[n] = float(args[n])
        if self.shot_size and any(v > 1 for v in out.values()):
            out["width"], out["height"] = self.shot_size
        return out


def text(value: Any) -> list[dict]:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=1)
    return [{"type": "text", "text": value}]


def handle_tool(client: Client, name: str, args: dict) -> list[dict]:
    if name == "ictrl_status":
        return text(client.call("GET", "/api/status"))
    if name == "ictrl_devices":
        return text(client.call("GET", "/api/devices"))
    if name == "ictrl_connect":
        body = {"transport": args.get("transport", "usb")}
        if args.get("udid"):
            body["udid"] = args["udid"]
        result = client.call("POST", "/api/connect", body, timeout=120)
        if isinstance(result, dict) and not result.get("pages"):
            # Nobody sees the phone yet: the agent shows the screen in its own
            # in-app browser (Claude's browser pane, Codex's), so the user can watch.
            return text(result) + text(
                f"iCtrl の画面がまだ開いていません。{client.url}/ を自分のアプリ内ブラウザー"
                "（Claude のブラウザー画面、Codex のブラウザーなど）で開いて、ユーザーが操作の様子を見られるようにしてください。")
        return text(result)
    if name == "ictrl_disconnect":
        return text(client.call("POST", "/api/disconnect", {}))
    if name == "ictrl_finish":
        result = client.call("POST", "/api/automation/stop", {"by": "agent"})
        return text("UI 自動操作を終了しました（iCtrl の画面が見えていなければ、iPhone との接続も切ります）")
    if name == "ictrl_screenshot":
        size = int(args.get("max_size", 1024))
        data, headers = client.call("GET", f"/api/screenshot?format=jpeg&max={size}&quality=80", raw=True)
        w, h = int(headers.get("x-width", 0)), int(headers.get("x-height", 0))
        client.shot_size = (w, h)
        return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": "image/jpeg"},
                {"type": "text", "text": f"screenshot {w}x{h}px（タップ座標はこの画像のピクセルで指定）"}]
    if name == "ictrl_elements":
        data = client.call("GET", "/api/elements", timeout=120)
        items = data.get("elements", [])
        notes = []
        if data.get("source") == "ocr":
            notes.append("文字認識（OCR）で読んだ画面の文字です（WebDriverAgent 不使用"
                         + (f": {data['wda_error']}" if data.get("wda_error") else "")
                         + "）。文字のないアイコンは含まれないので、必要ならスクリーンショットで確認する。")
        if data.get("locked"):
            notes.append("iPhone はロック中です。GUI でロック解除の自動入力が有効なら iCtrl が処理します。"
                         "無効・停止中の場合はユーザーに解除を依頼してください（AI はパスコードを入力しない）。")
        if data.get("alert"):
            a = data["alert"]
            notes.append(f"アラート表示中: {a.get('text')!r} ボタン: {a.get('buttons')}（ictrl_alert で押せる）")
        if data.get("modal"):
            notes.append(f"{data['modal']} が前面にあるため、その中の要素だけを表示しています。")
        if data.get("orientation"):
            notes.append(f"画面は横向き（{data['orientation']}）。座標は縦向きの画面基準に変換済み。")
        q = (args.get("filter") or "").lower()
        if q:
            items = [e for e in items if q in e["label"].lower()]
        lines = [f'{e["type"]} | {e["label"]}' + (f' = {e["value"]}' if e.get("value") else "")
                 + f' | ({e["x"]:.3f}, {e["y"]:.3f})' for e in items]
        return text("\n".join(notes + [f"{len(lines)} 要素（座標は画面に対する割合 0..1）"] + lines))
    if name == "ictrl_wait_for":
        return text(client.call("POST", "/api/wait_for", {"text": args["text"], "gone": bool(args.get("gone")),
                                                          "timeout": float(args.get("timeout", 10))}, timeout=90))
    if name == "ictrl_scroll_to":
        return text(client.call("POST", "/api/scroll_to", {"text": args["text"],
                                                           "direction": args.get("direction", "down"),
                                                           "max": int(args.get("max", 12))}, timeout=120))
    if name == "ictrl_alert":
        if args.get("action"):
            return text(client.call("POST", "/api/alert", {"action": args["action"], "name": args.get("name")}))
        return text(client.call("GET", "/api/alert"))
    if name == "ictrl_open_url":
        return text(client.call("POST", "/api/open_url", {"url": args["url"]}))
    if name == "ictrl_terminate_app":
        return text(client.call("POST", "/api/terminate", {"bundle_id": args["bundle_id"]}))
    if name == "ictrl_pinch":
        return text(client.call("POST", "/api/pinch", {"scale": float(args["scale"])}))
    if name == "ictrl_rotate":
        return text(client.call("POST", "/api/rotate", {"degrees": float(args["degrees"])}))
    if name == "ictrl_orientation":
        if args.get("set"):
            return text(client.call("POST", "/api/orientation", {"orientation": args["set"]}))
        return text(client.call("GET", "/api/orientation"))
    if name == "ictrl_screenshot_marks":
        size = int(args.get("max_size", 1024))
        data = client.call("GET", f"/api/screenshot_marks?max={size}", timeout=120)
        listing = "\n".join(f'{m["n"]}: {m["type"]} | {m["label"]}' for m in data["marks"])
        return [{"type": "image", "data": data["image_b64"], "mimeType": "image/png"},
                {"type": "text", "text": f"{len(data['marks'])} 個の番号（ictrl_tap_mark で押す）\n" + listing}]
    if name == "ictrl_tap_mark":
        return text(client.call("POST", "/api/tap_mark", {"n": int(args["n"])}))
    if name == "ictrl_tap_element":
        return text(client.call("POST", "/api/tap_element",
                                {"text": args.get("texts") or args["text"], "index": int(args.get("index", 0))},
                                timeout=120))
    if name == "ictrl_tap":
        body = client.coords(args, "x", "y")
        body["hold"] = int(args.get("hold_ms", 50)) / 1000
        body["count"] = int(args.get("count", 1))
        return text(client.call("POST", "/api/tap", body))
    if name == "ictrl_swipe":
        body = client.coords(args, "x1", "y1", "x2", "y2")
        body["duration"] = int(args.get("duration_ms", 300)) / 1000
        return text(client.call("POST", "/api/swipe", body))
    if name == "ictrl_button":
        return text(client.call("POST", "/api/button", {"name": args["name"]}))
    if name == "ictrl_type_text":
        return text(client.call("POST", "/api/text", {"text": args["text"]}))
    if name == "ictrl_key":
        return text(client.call("POST", "/api/key", {"key": args["key"],
                                                    "modifiers": args.get("modifiers", [])}))
    if name == "ictrl_list_apps":
        apps = client.call("GET", "/api/apps", timeout=60)["apps"]
        q = (args.get("query") or "").lower()
        if q:
            apps = [a for a in apps if q in a["name"].lower() or q in a["bundle_id"].lower()]
        return text("\n".join(f"{a['name']}\t{a['bundle_id']}" for a in apps) or "該当なし")
    if name == "ictrl_launch_app":
        return text(client.call("POST", "/api/launch", {"bundle_id": args["bundle_id"]}))
    if name == "ictrl_clipboard":
        if "text" in args:
            return text(client.call("POST", "/api/clipboard", {"text": args["text"]}))
        data = client.call("GET", "/api/clipboard")
        item = data.get("item")
        if item and item.get("kind") == "image":
            png, _ = client.call("GET", f"/api/clipboard/item/{item['id']}", raw=True)
            out = [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
                   {"type": "text", "text": f"iPhone のクリップボード: 画像 {item['width']}x{item['height']}"}]
            if data.get("text"):
                out.append({"type": "text", "text": data["text"]})
            return out
        return text(data.get("text") or "（iPhone のクリップボードは空です）")
    if name == "ictrl_wda_ipa":
        from .shots import desktop
        from .wda import build_ipa
        info = build_ipa(str(desktop() / "iCtrl-WDA.ipa"), args.get("bundle_id") or "com.ictrl.wda")
        return text(f"{info['ipa']} を作りました（Appium の WebDriverAgent {info.get('release', '')}、BSD-3-Clause）。"
                    "Sideloadly か AltStore で自分の Apple ID を使って iPhone に入れ、"
                    "iPhone の 設定 › 一般 › VPN とデバイス管理 でその Apple ID を信頼してもらってください。")
    if name == "ictrl_open_window":
        # From an agent the screen is shown in the agent app's own browser
        # (Claude's browser pane, Codex's), not as a second app window: two
        # open screens each followed the keyboard and the IME.
        ensure_server(client.port)
        return text(f"{client.url}/ をアプリ内ブラウザーで開いてください（iCtrl のウィンドウは開いていません）")
    raise KeyError(name)


def run(port: int) -> None:
    client = Client(port)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, ident = msg.get("method"), msg.get("id")
        result: Any = None
        error = None
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "ictrl", "version": __version__},
                      "instructions": ("iPhone を操作するツール群。まず ictrl_connect（USB か Wi-Fi）、"
                                       "次に ictrl_elements で要素を読み、ictrl_tap_element / ictrl_type_text などで操作する。"
                                       "WebDriverAgent で要素が読める間は画面を見ない（ictrl_screenshot を撮らない）: 画面遷移は ictrl_wait_for で待ち、"
                                       "結果は ictrl_elements（filter で絞る）で確認する。ictrl_screenshot は、要素が OCR になった・目的の要素が一覧に無い・"
                                       "画像の中身で判断が要る、のどれかのときだけ使う。"
                                       "パスコード要求は、ユーザーが GUI で事前に有効にした対象だけ iCtrl が処理する。"
                                       "XCTest のほか、ロック解除・iPhone の確認画面も別途有効にできる。"
                                       "通話中はパスコード画面を取得できず、無人でのロック解除はできない。"
                                       "ユーザーが本体の時計画面または空の入力欄を確認し、GUI の「パスコードを入力」で一度だけ入力できる。"
                                       "設定変更やパスコードの取得・入力を AI から行わない。自動入力が無効・停止中の場合はユーザーに対応を頼む"
                                       "（iCtrl ウィンドウの「パスコードを入力」でも可）。"
                                       "頼まれた操作をすべて終えたら ictrl_finish を呼んで UI 自動操作を終了する。"
                                       "iCtrl を使い始めたら、iCtrl の画面（ictrl_open_window の URL）を自分のアプリ内ブラウザーで開き、"
                                       "ユーザーが操作の様子を見られるようにする（すでに開いているなら不要。ictrl_connect の結果が知らせる）。"
                                       "ユーザーは同じ画面を iCtrl の画面（アプリ内ブラウザーか iCtrl ウィンドウ）で見ている。")}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params", {})
            try:
                content = handle_tool(client, params.get("name"), params.get("arguments") or {})
                result = {"content": content, "isError": False}
            except KeyError as e:
                error = {"code": -32602, "message": f"unknown tool or argument: {e}"}
            except Exception as e:
                result = {"content": text(f"エラー: {e}"), "isError": True}
        elif method == "ping":
            result = {}
        elif ident is None:
            continue  # notifications (initialized, cancelled, ...)
        else:
            error = {"code": -32601, "message": f"method not found: {method}"}
        if ident is None:
            continue
        reply = {"jsonrpc": "2.0", "id": ident}
        reply["error" if error else "result"] = error or result
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()
    # The agent's MCP session ended (stdin closed): its UI automation ends too,
    # so iOS's "Automation Running" overlay does not outlive the agent.
    with contextlib.suppress(Exception):
        client.call("POST", "/api/automation/stop", {"by": "agent"})
