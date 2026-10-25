// Display language: Japanese (as written in index.html and app.js) or English.
// The setting is "auto" (the browser's language), "ja" or "en"; it is kept in
// iCtrl's settings and cached here so the first paint is already right.
const CACHE = "ictrl.lang";

export function resolve(setting) {
  if (setting === "ja" || setting === "en") return setting;
  return (navigator.language || "").toLowerCase().startsWith("ja") ? "ja" : "en";
}

function cached() {
  try { return localStorage.getItem(CACHE) || "auto"; } catch (_) { return "auto"; }
}

export let SETTING = cached();
export let LANG = resolve(SETTING);

// Take a saved setting (this window's choice, or another window's). Returns
// whether the language changed: the caller then relabels the page in place.
export function adopt(setting) {
  SETTING = setting || "auto";
  try { localStorage.setItem(CACHE, SETTING); } catch (_) {}
  const lang = resolve(SETTING);
  if (lang === LANG) return false;
  LANG = lang;
  return true;
}

export function t(ja, vars) {
  let text = LANG === "en" && EN[ja] !== undefined ? EN[ja] : ja;
  if (vars) for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, v);
  return text;
}

// Messages from the server (errors, reasons) stay Japanese there, because the
// AI reads the same text through MCP; the page shows them in its language.
// "{x}" in a key matches any text, carried into the English.
let patterns = null;
export function fromServer(message) {
  if (LANG !== "en" || typeof message !== "string") return message;
  if (SERVER[message] !== undefined) return SERVER[message];
  patterns ??= Object.entries(SERVER).filter(([ja]) => ja.includes("{")).map(([ja, en]) => {
    const names = [];
    const source = ja.split(/\{(\w+)\}/).map((part, i) => {
      if (i % 2) { names.push(part); return "(.*?)"; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("");
    return { re: new RegExp(`^${source}$`, "s"), names, en };
  });
  for (const { re, names, en } of patterns) {
    const m = message.match(re);
    if (m) return names.reduce((text, name, i) => text.replace(`{${name}}`, m[i + 1]), en);
  }
  return message;
}

// index.html and text app.js has put on the page: each text node and label
// is looked up by its Japanese text, or by its English text (JA_OF) when the
// page is being switched back. Paragraphs with markup inside carry data-i18n
// and are replaced whole. The user's own data (clipboard items, app names,
// device details, the save folder) is never touched; device names in the
// list do not match any entry.
const ORIGINAL = new WeakMap();
const OWN_DATA = ".clips li:not(.muted), .apps li:not(.muted), #facts dd, #shotFolder";
let JA_OF = null;
function relabel(text) {
  JA_OF ??= Object.fromEntries(Object.entries(EN).map(([ja, en]) => [en, ja]));
  const ja = EN[text] !== undefined ? text : JA_OF[text];
  return ja === undefined ? null : LANG === "en" ? EN[ja] : ja;
}

export function translatePage(root = document.body) {
  document.documentElement.lang = LANG;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => node.parentElement?.closest(OWN_DATA) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue.trim();
    const next = text && relabel(text);
    if (next && next !== text) node.nodeValue = node.nodeValue.replace(text, next);
  }
  for (const el of root.querySelectorAll("[title], [placeholder], [aria-label]")) {
    if (el.closest(OWN_DATA) && !el.matches("button")) continue;
    for (const attr of ["title", "placeholder", "aria-label"]) {
      const value = el.getAttribute(attr);
      const next = value && relabel(value);
      if (next && next !== value) el.setAttribute(attr, next);
    }
  }
  for (const el of root.querySelectorAll("[data-i18n]")) {
    if (!ORIGINAL.has(el)) ORIGINAL.set(el, el.innerHTML);
    el.innerHTML = LANG === "en" && EN_HTML[el.dataset.i18n] ? EN_HTML[el.dataset.i18n] : ORIGINAL.get(el);
  }
}

const SERVER = {
  // connecting (manager.py, session.py, host.py)
  "USB でつながった iPhone がありません。ケーブルを確かめてください": "No iPhone is connected over USB. Check the cable",
  "Wi-Fi 操作の準備ができていません。一度 USB で接続すると自動で準備されます": "Wi-Fi is not set up yet. Connect once over USB and it is set up automatically",
  "Wi-Fi で iPhone を探すには Apple Devices アプリ（または iTunes）が必要です": "Finding the iPhone over Wi-Fi needs the Apple Devices app (or iTunes)",
  "Wi-Fi で iPhone が見つかりません。同じネットワークにあるか、iPhone の画面が点いているか確かめてください":
    "No iPhone found over Wi-Fi. Check that it is on the same network and that its screen is on",
  "USBで接続されたiPhoneがありません": "No iPhone is connected over USB",
  "Wi-Fiで見えるiPhoneがありません": "No iPhone is visible over Wi-Fi",
  "接続を中止しました": "Connection cancelled",
  "iPhoneに接続されていません（先に接続してください）": "Not connected to an iPhone (connect first)",
  "iPhoneに接続されていません": "Not connected to an iPhone",
  "このiPhoneは別のプロセスが操作中です。他の操作アプリやテストを終了してから接続してください。":
    "Another process is controlling this iPhone. Quit other control apps or tests, then connect.",
  "iPhone との接続が切れました。ケーブルが抜けていないか確かめて、もう一度接続してください":
    "The connection to the iPhone was lost. Check that the cable is plugged in, then connect again",
  "iPhone との接続が切れました。iPhone が同じ Wi-Fi にあるか、画面が点いているか確かめて、もう一度接続してください":
    "The connection to the iPhone was lost. Check that it is on the same Wi-Fi and that its screen is on, then connect again",
  "iPhone に接続できませんでした。もう一度接続してください（続くときはケーブルを挿し直すか、iPhone を再起動してください）":
    "Could not connect to the iPhone. Connect again (if it keeps happening, replug the cable or restart the iPhone)",
  "iPhoneのデベロッパモードを有効にしてください（設定 > プライバシーとセキュリティ）":
    "Turn on Developer Mode on the iPhone (Settings > Privacy & Security)",
  "iCtrl の部品の版が合わないため接続できません。iCtrl を更新してください（プラグインは「claude plugin update ictrl@ictrl」、アプリとして入れた場合は setup.ps1 をもう一度実行）":
    "Cannot connect because iCtrl's components do not match. Update iCtrl (the plugin: \"claude plugin update ictrl@ictrl\"; installed as an app: run setup.ps1 again)",
  "iPhone の画面を操作する準備ができませんでした。もう一度接続してください": "Could not get ready to control the iPhone's screen. Connect again",
  "USB でつながった iPhone が見つかりません。ケーブルと、Apple Devices アプリが入っているかを確かめてください":
    "No iPhone found over USB. Check the cable and that the Apple Devices app is installed",
  "iPhone から応答がありません。ケーブルを挿し直して、もう一度接続してください": "The iPhone does not respond. Replug the cable and connect again",
  "iPhone で「このコンピュータを信頼」を許可してください（{error}）": "On the iPhone, allow \"Trust This Computer\" ({error})",
  "この PC はまだ iPhone に信頼されていません。USB でつなぎ、iPhone で「信頼」を押してください":
    "The iPhone does not trust this PC yet. Connect over USB and tap \"Trust\" on the iPhone",
  "Wi-Fi で iPhone に接続できませんでした。iPhone の画面を点けて、もう一度接続してください": "Could not connect to the iPhone over Wi-Fi. Turn on its screen and connect again",
  "iPhone のマイクかカメラが使用中（通話・カメラ・録音など）のため、iOS が画面の映像を出せません":
    "iOS cannot give the screen video while the iPhone's microphone or camera is in use (a call, the camera, recording)",
  "iPhone との接続が切れました（画面の映像が届かなくなりました）": "The connection to the iPhone was lost (the screen video stopped arriving)",
  "iPhone との接続が終わりました": "The connection to the iPhone ended",
  "iPhone に操作を送れませんでした": "Could not send the operation to the iPhone",
  "iPhone との接続が切れました（操作を送れなくなりました）": "The connection to the iPhone was lost (operations could not be sent)",
  "iPhone のロック状態を確認できませんでした": "Could not check whether the iPhone is locked",
  "iPhone にキーボードをつなげませんでした。もう一度試してください": "Could not connect the keyboard to the iPhone. Try again",
  "iPhone がロックされているため、アプリを開けません。ロックを解除してください": "The iPhone is locked, so the app cannot open. Unlock it",
  "iPhone はロック中です。ロック解除はユーザーに依頼してください（パスコードは入力しません）": "The iPhone is locked. Ask the user to unlock it (the passcode is not entered)",
  "iPhoneからの応答がタイムアウトしました": "The iPhone did not respond in time",
  "先に USB で接続してください": "Connect over USB first",
  "ペアリングできませんでした: {error}": "Could not pair: {error}",
  "ペアリングの記録を保存できませんでした": "Could not save the pairing record",
  // clipboard
  "iPhone のクリップボードは空です（または対応していない形式です）": "The iPhone's clipboard is empty (or in an unsupported format)",
  "PC のクリップボードは空です": "The PC's clipboard is empty",
  "PC のクリップボードが他のアプリに使用中です": "Another app is using the PC's clipboard",
  "履歴にありません": "Not in the history",
  "クリップボード取得を停止しました": "Stopped getting the clipboard",
  "クリップボード取得が中止されました": "Getting the clipboard was cancelled",
  "クリップボード取得を停止しました（WDAの状態が変わりました）": "Stopped getting the clipboard (WDA changed state)",
  "iPhoneのロック状態を確認できないため、クリップボード取得を停止しました": "Stopped getting the clipboard: could not check whether the iPhone is locked",
  "iPhoneの画面を確認できないため、クリップボード取得を停止しました": "Stopped getting the clipboard: could not check the iPhone's screen",
  "ロック画面のため、クリップボード取得を停止しました": "Stopped getting the clipboard: the Lock Screen is shown",
  "ictrlcopy の呼び出しはキーボード接続だけに対応しています": "Running ictrlcopy works only with the keyboard method",
  // screenshots
  "保存先の変更は iCtrl の画面から行ってください": "Change the save folder from the iCtrl screen",
  "保存したスクリーンショットがありません": "No screenshot has been saved",
  "保存先はフォルダーの完全なパスで指定してください": "Give the save folder as a full path",
  "保存先がフォルダーではありません": "The save location is not a folder",
  "{name} が見つかりません": "{name} was not found",
  "エクスプローラーで開けませんでした ({code})": "Could not open it in File Explorer ({code})",
  "フォルダーの選択画面を開けませんでした ({code})": "Could not open the folder picker ({code})",
  "フォルダーを選べませんでした ({code})": "Could not choose the folder ({code})",
  "選んだ場所はフォルダーとして使えません": "The chosen place cannot be used as a folder",
  // passcode
  "パスコードの操作は iCtrl のウィンドウからだけ行えます（AI からは使えません）": "Passcode actions work only from the iCtrl window (not from the AI)",
  "このパスコード操作は利用できません": "This passcode action is not available",
  "XCTest 以外のパスコード入力には対応していません": "Only XCTest passcode requests are supported",
  "自動入力の対象は XCTest の要求だけです": "Automatic entry is only for XCTest requests",
  "Windows 資格情報を読み取れませんでした": "Could not read the Windows credential",
  "Windows 資格情報を保存できませんでした": "Could not save the Windows credential",
  "Windows 資格情報を削除できませんでした": "Could not delete the Windows credential",
  "パスコードを入力中です": "The passcode is being entered",
  "パスコードは英数字 4〜32 文字で指定してください": "The passcode must be 4 to 32 letters or digits",
  "パスコードの入力に失敗しました（再試行はしません）": "Entering the passcode failed (no retry)",
  "自動入力の設定を読み取れません。ウィンドウで設定し直してください": "Cannot read the automatic entry setting. Set it again in the window",
  "自動入力を使う iPhone に接続してください": "Connect the iPhone to use automatic entry with",
  "先にパスコードを保存してください": "Save the passcode first",
  "保存済みパスコードを確認し、設定し直してください": "Check the saved passcode and set it again",
  "別のパスコード入力または設定変更のため停止しました": "Stopped for another passcode entry or a settings change",
  "入力または映像・通話の状態が変わったため停止しました": "Stopped because input, video or call state changed",
  "XCTest のパスコード入力画面を確認できないため、入力しません": "The XCTest passcode screen could not be confirmed, so nothing is entered",
  "保存済みパスコードを確認してください": "Check the saved passcode",
  "自動入力が停止されたため、入力しませんでした": "Automatic entry was stopped, so nothing was entered",
  "XCTest の入力画面が変わったため、入力しませんでした": "The XCTest entry screen changed, so nothing was entered",
  "XCTest の自動入力を停止しました。アクセシビリティで入力画面を確認できませんでした": "Stopped XCTest automatic entry: accessibility could not confirm the entry screen",
  "パスコード入力を停止しました": "Stopped entering the passcode",
  "キーの状態が変わったため、パスコード入力を停止しました": "Stopped entering the passcode because the key state changed",
  "パスコード入力を停止しました（再送はしません）": "Stopped entering the passcode (not sent again)",
  // WebDriverAgent
  "iCtrl WDA（WebDriverAgent）が iPhone に入っていません。README の「補助アプリ（WebDriverAgent）を入れる」の手順で入れてください":
    "iCtrl WDA (WebDriverAgent) is not on the iPhone. Install it with the README's steps for the helper app (WebDriverAgent)",
  "iCtrl WDA（WebDriverAgent）が {seconds} 秒以内に起動しませんでした。XCTest のパスコード要求が出ている場合は、iCtrl のパスコード設定と自動入力の状態を確認してください。要求が出ていなければ、Sideloadly か AltStore で iCtrl WDA を入れ直してください: {detail}":
    "iCtrl WDA (WebDriverAgent) did not start within {seconds} seconds. If an XCTest passcode request is shown, check iCtrl's passcode setting and automatic entry. If not, reinstall iCtrl WDA with Sideloadly or AltStore: {detail}",
  "WebDriverAgent の応答が途中で切れました（終了した可能性があります）": "WebDriverAgent's response was cut off (it may have ended)",
  "iCtrl WDA（WebDriverAgent）の署名が切れているか、信頼されていないため起動できません。Sideloadly か AltStore で iCtrl WDA を入れ直してください（AltStore なら更新）: {detail}":
    "iCtrl WDA (WebDriverAgent) cannot start: its signature expired or it is not trusted. Reinstall iCtrl WDA with Sideloadly or AltStore (refresh it in AltStore): {detail}",
  "iCtrl WDA（WebDriverAgent）が起動直後に終了しました。iPhone のロックを解除してから、もう一度試してください（続く場合は Sideloadly か AltStore で入れ直す）: {detail}":
    "iCtrl WDA (WebDriverAgent) ended right after starting. Unlock the iPhone and try again (if it continues, reinstall it with Sideloadly or AltStore): {detail}",
  "Windows の文字認識（OCR）が使えません": "Windows text recognition (OCR) is not available",
};

const EN_HTML = {
  shIntro: "To bring text you copy on the iPhone to the PC right away, the iPhone needs the shortcut \"<b>ictrlcopy</b>\". It was not found, or it did not respond.",
  sh1: "On the iPhone, open the <b>Shortcuts</b> app, tap <b>+</b> at the bottom, then <b>Edit</b> at the top right",
  sh2: "Add these actions in order: <b>Get Clipboard</b> → <b>Get Text from Input</b> → <b>If</b> (condition \"Text\" \"has any value\")",
  sh3: "Put <b>Copy to Clipboard</b> inside the If (leave \"Otherwise\" empty)",
  sh4: "For the keyboard method, add <b>Automation</b> → <b>Keyboard</b> (when connected) and turn it on. Turn <b>Notify</b> off",
  sh5: "Name it <b>ictrlcopy</b> and save",
};

const EN = {
  // index.html
  "未接続": "Offline",
  "AI が操作中": "AI at work",
  "映像の統計": "Video statistics",
  "接続方法": "Connection method",
  "切断": "Disconnect",
  "メニュー (Ctrl+B)": "Menu (Ctrl+B)",
  "iPhone の画面": "iPhone screen",
  "iPhone を接続": "Connect an iPhone",
  "USB ケーブルまたは同じ Wi-Fi で接続できます。": "Connect with a USB cable or over the same Wi-Fi.",
  "USB で接続": "Connect via USB",
  "Wi-Fi で接続": "Connect via Wi-Fi",
  "画面を映す": "Show the screen",
  "ホーム (Ctrl+H)": "Home (Ctrl+H)",
  "アプリ切替": "App switcher",
  "iPhone をロック": "Lock the iPhone",
  "画面を映すのをやめる": "Stop showing the screen",
  "スクリーンショットを保存 (Ctrl+S)": "Save a screenshot (Ctrl+S)",
  "日本語 / 英字入力の切替": "Switch Japanese / English input",
  "PC キーボードを接続 (Ctrl+K)": "Connect the PC keyboard (Ctrl+K)",
  "メニュー": "Menu",
  "接続": "Connection",
  "端末": "Device",
  "検出中…": "Searching…",
  "端末一覧を更新": "Refresh the device list",
  "接続したら画面を映す": "Show the screen when connected",
  "下のバーに「A / あ」を表示": "Show \"A / あ\" in the bottom bar",
  "使っていなくても接続を続ける（非推奨）": "Keep the connection even when unused (not recommended)",
  "オフのときは、AI が操作を終えたとき、人も AI も操作しないまま 5 分たったとき、画面を閉じるか隠して 30 秒たったときに、iPhone との接続を自動で切ります。":
    "When off, iCtrl closes the connection to the iPhone when the AI finishes, after 5 minutes with no operation by a person or the AI, and 30 seconds after the screen is closed or hidden.",
  "オフ（おすすめ）のときは、AI が操作を終えたとき、人の操作も AI の操作もないまま 5 分たったとき、iCtrl の画面を閉じるか隠して 30 秒たったときに、iPhone との接続を切ります":
    "When off (recommended), iCtrl closes the connection when the AI finishes, after 5 minutes with no operation by a person or the AI, and 30 seconds after the iCtrl screen is closed or hidden",
  "言語": "Language",
  "自動": "Auto",
  "iPhone の「ペアリング済みのコンピュータ」に iCtrl として表示されるよう、iCtrl 専用の「信頼」を作れます（iPhone で「信頼」とパスコード入力が一度必要。他のアプリの信頼はそのまま）。":
    "You can create a trust for iCtrl alone, so it appears as iCtrl among the iPhone's paired computers (needs \"Trust\" and the passcode on the iPhone once; other apps' trusts stay as they are).",
  "iCtrl 専用の信頼を作成": "Create iCtrl's own trust",
  "クリップボード": "Clipboard",
  "iPhone でコピーしたら PC に自動で反映": "Copy to the PC automatically when you copy on the iPhone",
  "iPhone のクリップボードを今すぐ PC へ": "Bring the iPhone's clipboard to the PC now",
  "iPhone でコピーするとここに並びます": "Things you copy on the iPhone appear here",
  "スクリーンショット": "Screenshots",
  "保存先": "Save to",
  "読み込み中…": "Loading…",
  "変更…": "Change…",
  "開く": "Open",
  "ピクチャの iCtrl フォルダーに戻す": "Go back to the iCtrl folder in Pictures",
  "既定に戻す": "Reset",
  "アプリ": "Apps",
  "読み込む": "Load",
  "アプリを検索": "Search apps",
  "接続後に「読み込む」": "Connect, then press \"Load\"",
  "AI から操作": "Control from an AI",
  "Claude Code / Codex に MCP サーバーとして登録すると、AI がこの画面を見ながら iPhone を操作できます。":
    "Register iCtrl as an MCP server in Claude Code / Codex, and the AI can operate the iPhone while watching this screen.",
  "Claude Code 用をコピー": "Copy for Claude Code",
  "Codex 用をコピー": "Copy for Codex",
  "AI が画面の要素を読むときだけ、iPhone の UI 自動操作（WebDriverAgent）が動き、iPhone に「Automation Running」と出ます（iOS の表示で、日本語にも非表示にもできません）。接続を切るか、下のボタンで消えます。iPhone 単体では、音量ボタンを両方長押しでも止まります。":
    "Only while the AI reads the screen's elements, the iPhone's UI automation (WebDriverAgent) runs and the iPhone shows \"Automation Running\" (an iOS label that cannot be hidden). It goes away when you disconnect or press the button below. On the iPhone itself, holding both volume buttons also stops it.",
  "自動操作を終了（Automation Running を消す）": "End automation (clear Automation Running)",
  "画面要素の読み取り（WebDriverAgent）": "Reading screen elements (WebDriverAgent)",
  "AI が使うときだけ起動（既定）": "Start only when the AI uses it (default)",
  "使わない（文字認識のみ・パスコード要求なし）": "Do not use (text recognition only, no passcode requests)",
  "WDA のパスコード入力": "Passcode entry for WDA",
  "パスコードはこの PC の Windows 資格情報マネージャーに保存され、AI には渡しません。":
    "The passcode is kept in this PC's Windows Credential Manager and is never given to the AI.",
  "パスコードを入力": "Enter the passcode",
  "自動入力が動かないときに使えます。XCTest のパスコード画面を確認し、未入力の場合だけ送信します。":
    "Use this when automatic entry does not work. It checks the XCTest passcode screen and sends only if nothing has been entered.",
  "iPhone のパスコード": "iPhone passcode",
  "保存": "Save",
  "保存したパスコードを削除": "Delete the saved passcode",
  "この iPhone の XCTest 要求に自動入力": "Enter it for this iPhone's XCTest requests",
  "設定を読み込んでいます…": "Loading settings…",
  "WDA 起動時の XCTest 要求を画面で確認し、1 回だけ入力します。WDA の起動成功を確認して、次の要求に備えます。":
    "When WDA starts, it checks the XCTest request on screen and enters the passcode once. It confirms WDA started before getting ready for the next request.",
  "確認して再有効化": "Check and turn on again",
  "コピー": "Copy",
  "切り取り": "Cut",
  "貼り付け": "Paste",
  "すべて選択": "Select all",
  "元に戻す": "Undo",
  "やり直し": "Redo",
  "ここを長押し": "Long-press here",
  "メニュー/選択": "Menu / select",
  "単語を選択": "Select a word",
  "ダブルクリック": "Double-click",
  "スクリーンショットを保存": "Save a screenshot",
  "スクリーンショットのフォルダーを開く": "Open the screenshot folder",
  "画面を PC にコピー": "Copy the screen to the PC",
  "ホームへ": "Go home",
  "iPhone にショートカットを追加してください": "Add a shortcut on the iPhone",
  "AI（Claude / Codex）に「iCtrl のショートカットを作って」と頼むと、iCtrl 経由で代わりに作成することもできます。":
    "You can also ask an AI (Claude / Codex) to \"make the iCtrl shortcut\", and it builds it for you through iCtrl.",
  "追加したので試す": "I added it, try again",
  "閉じる": "Close",

  // app.js
  "接続を準備中": "Preparing the connection",
  "USB の iPhone を探しています": "Looking for the iPhone over USB",
  "Wi-Fi で iPhone を探しています": "Looking for the iPhone over Wi-Fi",
  "Wi-Fi で接続中": "Connecting over Wi-Fi",
  "Wi-Fi で使う準備中": "Preparing Wi-Fi use",
  "iPhone に接続中": "Connecting to the iPhone",
  "iPhone で「このコンピュータを信頼」を押してください": "On the iPhone, tap \"Trust This Computer\"",
  "デベロッパモードを確認中": "Checking Developer Mode",
  "iPhone の画面機能を準備中": "Preparing the iPhone's screen features",
  "iPhone との通信を準備中": "Preparing communication with the iPhone",
  "画面の情報を取得中": "Getting screen information",
  "画面の映像を開始中": "Starting the screen video",
  "操作の準備中": "Preparing controls",
  "準備完了": "Ready",
  "iPhone SE (第3世代)": "iPhone SE (3rd generation)",
  "切断しました": "Disconnected",
  "切断中…": "Disconnecting…",
  "接続中…": "Connecting…",
  "接続が切れました": "Connection lost",
  "画面を映すのをやめる（操作・AI・スクリーンショットは続けて使えます）": "Stop showing the screen (controls, the AI and screenshots keep working)",
  "画面を映していません": "The screen is not shown",
  "カメラの使用中は画面を映していません": "The screen is not shown while the camera is in use",
  "マイクかカメラの使用中は映像を出せません": "No video while the microphone or camera is in use",
  "映像を待っています…": "Waiting for video…",
  "接続しています": "Connecting",
  "原因を確認してから、もう一度接続してください。": "Check the cause, then connect again.",
  "操作・AI・スクリーンショットはそのまま使えます。映すときは「画面を映す」を押してください。":
    "Controls, the AI and screenshots keep working. To show it, press \"Show the screen\".",
  "iOS は、画面の映像を流している間はカメラを使わせません（カメラが真っ暗になります）。カメラを閉じると映像に戻ります。操作・AI・スクリーンショットはそのまま使えます。":
    "iOS does not allow the camera while the screen is streamed (the camera goes black). Video returns when you close the camera. Controls, the AI and screenshots keep working.",
  "iOS は、通話や録音などでマイクかカメラを使っている間、画面の映像を出しません。操作・AI・スクリーンショットは使えます。WDA の起動に必要な XCTest 要求には、メニューで有効にした自動入力を使います。終わったら「画面を映す」を押してください。":
    "iOS gives no screen video while the microphone or camera is in use, such as during a call or recording. Controls, the AI and screenshots still work. XCTest requests needed to start WDA use the automatic entry turned on in the menu. When done, press \"Show the screen\".",
  "画面が変化すると表示されます。": "It appears when the screen changes.",
  "{via} でつないでいます。": "Connecting over {via}.",
  "監視中": "Watching",
  "監視停止": "Stopped",
  "画面": "Screen",
  "映像": "Video",
  "待機中": "Idle",
  "iCtrl のサーバーが止まっています。接続ボタンを押すと起動します": "The iCtrl server is not running. Press a connect button to start it",
  "iCtrl のサーバーを起動しています…": "Starting the iCtrl server…",
  "iCtrl のサーバーを起動できませんでした。Claude などの AI に「iCtrl につないで」と頼むと起動し、この画面も自動でつながります":
    "Could not start the iCtrl server. Ask an AI such as Claude to \"connect to iCtrl\": it starts the server, and this screen reconnects by itself",
  "日本語入力": "Japanese input",
  "英字入力": "English input",
  "（クリック / 半角全角キーで切替。Windows と iPhone の両方が切り替わります）": " (click or the Hankaku/Zenkaku key to switch; both Windows and the iPhone switch)",
  "PC キーボードを iPhone に接続しました（Ctrl+K で解除）": "The PC keyboard is connected to the iPhone (Ctrl+K to release)",
  "キーボードの接続を解除しました": "The keyboard was released",
  "この画面では HEVC の映像を再生できません（GPU で HEVC を再生できるブラウザーが必要です）": "This screen cannot play HEVC video (a browser that plays HEVC on the GPU is needed)",
  "この画面では映像を再生できません（WebCodecs に対応したブラウザーが必要です）": "This screen cannot play video (a browser with WebCodecs is needed)",
  "iPhone の画面を PC にコピーしました": "Copied the iPhone's screen to the PC",
  "貼り付けできませんでした: ": "Could not paste: ",
  "先に iPhone に接続してください": "Connect an iPhone first",
  "iPhone はすでにロックされています": "The iPhone is already locked",
  "画面を映しました": "Showing the screen",
  "画面を映すのをやめました": "Stopped showing the screen",
  "iPhone の自動操作を終了しました": "Ended the iPhone's automation",
  "iPhone で「このコンピュータを信頼」→ パスコードを入力してください": "On the iPhone, tap \"Trust This Computer\", then enter the passcode",
  "iCtrl 専用の信頼を作成しました。次の接続から使います": "Created iCtrl's own trust. It is used from the next connection",
  "この環境では利用不可": "Not available here",
  "保存済み": "Saved",
  "未保存": "Not saved",
  "設定を取得できません。iCtrl サーバーとの接続を確認してください。": "Cannot get the settings. Check the connection to the iCtrl server.",
  "先にパスコードを保存してください。自動入力は未設定です。": "Save the passcode first. Automatic entry is not set up.",
  "パスコードを入力しています。確認が終わるまで追加の入力はしません。": "Entering the passcode. Nothing more is entered until it is confirmed.",
  "入力結果が未確認のため、自動入力は停止中です。iPhone の状態とパスコードを確認してから再有効化してください。":
    "Automatic entry is paused because the result was not confirmed. Check the iPhone and the passcode, then turn it on again.",
  "許可した iPhone の接続を待っています。自動入力はいつでもオフにできます。": "Waiting for the allowed iPhone to connect. You can turn automatic entry off at any time.",
  "自動入力はオフです。有効にする iPhone を接続してください。": "Automatic entry is off. Connect the iPhone to turn it on for.",
  "接続中の iPhone は許可した端末と違うため、入力しません。この端末で使う場合は再有効化してください。":
    "The connected iPhone is not the allowed one, so nothing is entered. To use it on this iPhone, turn it on again.",
  "この iPhone で有効です。WDA 起動時の XCTest 要求に自動入力します。": "On for this iPhone. The passcode is entered for XCTest requests when WDA starts.",
  "自動入力はオフです。オンにすると、WDA 起動時の XCTest 要求に自動入力します。": "Automatic entry is off. When on, the passcode is entered for XCTest requests when WDA starts.",
  "この環境では Windows 資格情報マネージャーを利用できません。": "Windows Credential Manager is not available here.",
  "パスコードを保存しました": "Saved the passcode",
  "削除しました": "Deleted",
  "この iPhone の XCTest 自動入力を有効にしました": "Turned on XCTest automatic entry for this iPhone",
  "自動入力をオフにしました": "Turned automatic entry off",
  "先にパスコードを保存してください": "Save the passcode first",
  "パスコードを入力しました": "Entered the passcode",
  "スクリーンショットを保存しました": "Saved the screenshot",
  "フォルダーを開く": "Open folder",
  " · コピー済み": " · copied",
  "取得できませんでした": "Could not get it",
  "保存先を既定に戻しました": "Reset the save folder",
  "保存先を変更しました": "Changed the save folder",
  "iPhone が見つかりません": "No iPhone found",
  "取得できません": "Not available",
  "PC にコピー": "Copy to the PC",
  "iPhone にコピー": "Copy to the iPhone",
  "画像": "Image",
  "{n} 文字": "{n} characters",
  "PC にコピーしました": "Copied to the PC",
  "iPhone にコピーしました": "Copied to the iPhone",
  "テキスト": "Text",
  "コピーしました": "Copied",
  "AI が要素を読むときに起動し、作業が終わると止めます。起動のたびに iPhone がパスコードを求めることがあります。":
    "Starts when the AI reads elements and stops when the work is done. The iPhone may ask for the passcode each time it starts.",
  "WebDriverAgent を使わず、画面の文字を Windows の文字認識で読みます。パスコード要求は出ませんが、遅く、文字のないアイコンは読めません。":
    "Reads the screen's text with Windows text recognition instead of WebDriverAgent. No passcode requests, but it is slower and cannot read icons without text.",
  "iPhone の画像を PC にコピーしました": "Copied the iPhone's image to the PC",
  "iPhone のテキストを PC にコピーしました": "Copied the iPhone's text to the PC",
  "ショートカットを呼び出せませんでした": "Could not run the shortcut",
  "ictrlcopy の実行を要求しました。iPhone の表示とコピー結果を確認してください": "Asked to run ictrlcopy. Check the iPhone and the copied result",
  "該当なし": "No matches",
  "クリックで起動": "Click to open",
  "{name} を起動しました": "Opened {name}",
  "再読み込み": "Reload",
  "コマンドをコピーしました。ターミナルに貼り付けて実行してください": "Copied the command. Paste it into a terminal and run it",
  "~/.codex/config.toml に貼り付けてください": "Paste it into ~/.codex/config.toml",
  "iPhone の UI 自動操作（Automation Running）が有効です。クリックで終了": "The iPhone's UI automation (Automation Running) is on. Click to end it",
  "タップ": "Tap",
  "スワイプ": "Swipe",
  "ボタン操作": "Button",
  "文字入力": "Typing",
  "キー入力": "Keys",
  "タッチ操作": "Touch",
  "アプリを起動": "Opening an app",
  "画面を確認": "Checking the screen",
  "アプリ一覧を取得": "Getting the app list",
  "クリップボードを操作": "Using the clipboard",
  "接続を操作": "Changing the connection",
  "画面の要素を確認": "Reading screen elements",
  "操作": "Operation",
};
