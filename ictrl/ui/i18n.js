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

export const SETTING = cached();
export const LANG = resolve(SETTING);

// Whether the saved setting differs from the one this page was drawn with
// (another window changed it): the caller reloads.
export function adopt(setting) {
  setting = setting || "auto";
  try { localStorage.setItem(CACHE, setting); } catch (_) {}
  return resolve(setting) !== LANG;
}

export function t(ja, vars) {
  let text = LANG === "en" && EN[ja] !== undefined ? EN[ja] : ja;
  if (vars) for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, v);
  return text;
}

// index.html: text nodes and labels are translated by their exact Japanese
// text; paragraphs with markup inside carry data-i18n and are replaced whole.
export function translatePage(root = document.body) {
  document.documentElement.lang = LANG;
  if (LANG !== "en") return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue.trim();
    if (text && EN[text] !== undefined) node.nodeValue = node.nodeValue.replace(text, EN[text]);
  }
  for (const el of root.querySelectorAll("[title], [placeholder], [aria-label]")) {
    for (const attr of ["title", "placeholder", "aria-label"]) {
      const value = el.getAttribute(attr);
      if (value && EN[value] !== undefined) el.setAttribute(attr, EN[value]);
    }
  }
  for (const el of root.querySelectorAll("[data-i18n]")) {
    if (EN_HTML[el.dataset.i18n]) el.innerHTML = EN_HTML[el.dataset.i18n];
  }
}

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
  "接続方法": "Connection",
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
  "iCtrl サーバーに再接続しています…": "Reconnecting to the iCtrl server…",
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
  "クリップボードを操作": "Clipboard",
  "接続を操作": "Connection",
  "画面の要素を確認": "Reading screen elements",
  "操作": "Operation",
};
