# iCtrl の技術資料

iCtrl の仕組み、手動のセットアップ、内部の動作、API などの詳しい説明です。導入と使い方は [README](../README.md) にあります。

## 仕組み

```
iPhone ──(USB / Wi-Fi: usbmux → lockdown → CoreDevice トンネル)──┐
                                                                  │
   iCtrl サーバー（127.0.0.1:47800, Python）                      │
   ├─ 画面: CoreDevice DisplayService の HEVC/RTP を受信 ─────────┘
   ├─ 入力: Universal HID（タッチ）/ Indigo（ボタン・キー）
   ├─ REST API ＋ WebSocket
   │
   ├─ GUI（ブラウザのアプリウィンドウ）… WebCodecs で GPU デコード、60fps
   └─ MCP サーバー（stdio）… Claude Code / Codex から操作
```

- 映像は iPhone が送る HEVC をそのまま GPU でデコードします。サーバー側で再エンコードしないので遅延が小さく、CPU もほとんど使いません。
- 使う GPU は自動で決まります（Windows の設定に従って選ばれます）。NVIDIA・AMD（Radeon）・Intel のどれでもよく、CPU に内蔵された GPU でもかまいません。HEVC の再生に対応した GPU が要ります（ここ数年の製品ならほぼ対応）。HEVC を CPU だけで再生する機能は無いため、CPU での再生は選べません。
- iCtrl をアプリとして使う場合（スタートメニューの「iCtrl」や `iCtrl.cmd` で開く場合）、ウィンドウは WebView2（Windows に入っている Edge の部品。Windows が自動で更新）で作っています（`window/iCtrlWindow.cs`）。Claude のプラグインとして使う場合はこのウィンドウを開かず、Claude のアプリ内ブラウザーに同じ画面を出します。Chrome は要りません。ブラウザーの機能（F5・Ctrl+F などのショートカット、ズーム、自動入力、パスワードの保存、ステータスバー、開発者ツール、右クリックメニューの「戻る」など）は止めてあり、外部のリンクは既定のブラウザーで開きます。ウィンドウは最初に開くときに Windows 標準の C# コンパイラー（.NET Framework）でビルドします（約 1 秒。`%LOCALAPPDATA%\iCtrl\window`）。`window\lib` の DLL は Microsoft の WebView2 SDK（再頒布可、1.0.4258.31）で、新しい版の iCtrl を出すときにだけ更新します。
- Edge のエンジンは、映像の大きさを渡さずに再生部品を準備すると、表示範囲を仮の 1280×720 のまま返し、画面の上 3 割ほどしか描画しません（Edge 155 で確認）。iCtrl は大きさ（`codedWidth`/`codedHeight`）を渡しています。
- GUI と AI は同じ接続を共有します。AI の操作はウィンドウ上に紫の点で表示され、いつでも人が操作を引き継げます。
- 切断は下位層のイベント（トンネル終了、映像受信終了、送信エラー）で検知します。一定時間の無通信を切断とはみなしません。自動再接続や入力の再送もしません。

## 必要なもの

- Windows 10/11
- **Python 3.13 以上（python.org 版。最新版でよい）**。`winget install Python.Python.3.14` か [python.org](https://www.python.org/downloads/) のインストーラーで入れます。iCtrl は Python を同梱しません。Microsoft Store 版は使えません（パッケージ化されたアプリなので AppData の書き込みが振り替えられ、設定とペアリング記録が別の場所に分かれるため）
- **Apple Devices アプリ**（[Microsoft Store](https://apps.microsoft.com/detail/9np83lwlpz9k)、または `winget install --id 9NP83LWLPZ9K --source msstore`。iTunes でも可）。Windows で iPhone と USB 通信するためのドライバとサービス（Apple Mobile Device Service）が入ります。アプリを開いて iPhone を「信頼」する必要はありません。
- iPhone 側: iOS 17.4 以降、**デベロッパモード** オン（設定 › プライバシーとセキュリティ › デベロッパモード）

### 初めて接続するとき

1. iPhone を USB で PC につなぎ、iCtrl で「USB で接続」を押す。
2. iPhone に「このコンピュータを信頼しますか？」が出たら「信頼」を押し、パスコードを入力する（iCtrl 専用の「信頼」を作ります。最初の一度だけ）。
3. 同時に Wi-Fi 用のペアリングも自動で作られます（iPhone 側の操作は不要）。以後は PC と iPhone が同じネットワークにあれば、ケーブルなしで「Wi-Fi で接続」できます。

Apple Devices などで既にこの PC を信頼している場合は、その信頼で接続できます。メニュー「接続」の「iCtrl 専用の信頼を作成」で、iCtrl 専用に切り替えられます（他のアプリの信頼はそのまま）。

### iPhone に表示されるコンピュータ名

iPhone の「設定 › デベロッパ › ペアリング済みのコンピュータ」に、iCtrl は「iCtrl」として表示されます。Wi-Fi で接続している間は「進行中、セッション開始: ◯秒前」と出て、開くと「“iCtrl”でこのiPhoneを表示およびコントロールしています」と表示されます（「セッションを中止」を押すと iCtrl のペアリングが解除されます）。USB の接続はここに記録されません（iCtrl は USB では lockdown 経由でトンネルを作るため、iPhone が接続元のコンピュータを特定しない。iPhone のログで確認）。iOS 27.0.1 では、Wi-Fi で接続して映像を流しても、ダイナミックアイランドに表示は出ません（iPhone のログには画面共有のインジケーターを開始したと記録される）。

- iCtrl は自分の名前と、インストールごとの固有の識別値でペアリングします。pymobiledevice3 は既定ではどのホストも同じ固定値（シリアル番号 AAAAAAAAAAAA など）で名乗るため、同じ PC で pymobiledevice3 を使う他のツール（AltStore の設定など）とまとめて1台のコンピュータとして表示されていました。記録は `%LOCALAPPDATA%\iCtrl\pairing` と `%LOCALAPPDATA%\iCtrl\pymobiledevice3` に、iCtrl 専用で置きます。

## セットアップ（Claude のプラグインを使わない場合）

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 -Shortcut
```

| オプション | 内容 |
|---|---|
| `-Shortcut` | スタートメニューに「iCtrl」を作る |
| `-Desktop` | デスクトップにも「iCtrl」を作る |
| `-Claude` | Claude Code に MCP サーバーと、応答が終わったら WDA を止めるフックを登録する |
| `-Codex` | Codex（`~/.codex/config.toml`）に MCP サーバーを登録する |

- 管理者権限は使いません。プログラムは取得したフォルダー（仮想環境 `.venv` を含む）に置きます。仮想環境は約 190MB です。依存ライブラリは `requirements.txt` で版を固定し、pymobiledevice3 は iCtrl が使わない依存（IPython・PyAV など）を入れないよう `--no-deps` で導入します。PyPI にソースしか無い lzfse・pylzss・hexdump は、配布元がビルドした `wheels\` の版を使うので、利用者の PC ではビルドしません（C 拡張は安定 ABI でビルドしてあり、Python 3.13 以降のどの版でも読み込めます。作り方は `tools\build_wheels.py`）。Python 3.13 と 3.14 で動作を確認しています。
- 「設定 → アプリ → インストールされているアプリ」に iCtrl が載り、そこからアンインストールできます（`uninstall.ps1`）。既定では設定とデータもすべて削除し、残すかどうかを最初に選べます。コマンドでは `-Quiet`（確認なし・すべて削除）、`-KeepSettings`（設定とデータを残す）。取得したソースのフォルダーは削除しないので、不要なら手で消してください。
- iCtrl のサーバーは、画面を開いたときや AI が使うときに起動し、使われなくなると終了します（画面が 1 つも開いておらず、iPhone とつながっておらず、AI の操作も無い状態が 1 分続いたとき）。アプリのウィンドウを閉じたときは、ほかの画面も AI の操作もなければ、待たずに iPhone との接続を切って終了します（ウィンドウはブラウザーの識別名に `iCtrlWindow` を付けて名乗る。再読み込みできないので、ページが閉じたらウィンドウを閉じたと分かる。新しい版の画面を読み込み直すときは、ページが先にそう伝える）。Windows の起動時に起動するオプションはありません。

### アプリとして入れた場合の起動

スタートメニューの「iCtrl」（または `iCtrl.cmd`）で、サーバーを起動してウィンドウを開きます。コマンドで起動するときは、iCtrl のフォルダーで次を実行します。

| コマンド | 動き |
|---|---|
| `.venv\Scripts\python.exe -m ictrl url` | サーバーだけを裏で起動し、画面の URL を表示する |
| `.venv\Scripts\python.exe -m ictrl serve` | サーバーを前面で動かす（ログがその場に出る。Ctrl+C で止まる） |
| `.\iCtrl.cmd` | サーバーを起動して、ウィンドウを開く |

更新は、取得したフォルダーを新しくして（`git pull`、または新しい ZIP を上書き）、`setup.ps1` をもう一度実行します。

### 変更するレジストリとファイル

| 種類 | 場所 |
|---|---|
| レジストリ（唯一） | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\iCtrl`（「インストールされているアプリ」の登録） |
| プログラム | 取得したフォルダー（`.venv` を含む）。プラグインの場合は Claude Code のプラグインのフォルダー（`.venv`、uv を取得した場合は `.uv\`） |
| 設定・端末の一覧 | `%APPDATA%\iCtrl\` |
| ログ・ペアリング記録・キャッシュ・ウィンドウのブラウザープロファイル・アイコン | `%LOCALAPPDATA%\iCtrl\` |
| 保存したパスコード（使う場合） | Windows 資格情報マネージャー（`iCtrl/...`） |
| ショートカット | スタートメニュー・デスクトップ（選んだ場合）の `iCtrl.lnk` |
| AI 連携（選んだ場合） | Claude Code の MCP 登録と `~/.claude/settings.json` の Stop フック、`~/.codex/config.toml`（プラグインの場合は Claude Code のプラグイン管理だけ） |

Codex などパッケージ化（MSIX）されたアプリから iCtrl が起動されると、そのアプリが書く AppData は Windows によってアプリ専用の場所へ振り替えられます。設定やペアリング記録が二重にならないよう、その場合 iCtrl のサーバーは Windows のシェル（エクスプローラー）経由で起動し、振り替えの外で動かします（プログラムのフォルダーの `.venv\iCtrl-server.lnk` を使います）。

## 接続できる範囲

iCtrl は、**USB で直接つながっているか、PC と同じネットワーク（同じ Wi-Fi など）にある iPhone** だけを対象にします。外出先からの接続（Tailscale などの VPN 経由）には対応しません。

- Wi-Fi で使うには、事前に一度 USB で接続して Wi-Fi 操作の準備（ペアリング記録）を済ませておきます。
- **接続すると、既定で画面を映します**（設定 `video_on_connect`。メニューの「接続したら画面を映す」）。オフでも操作・AI・スクリーンショットは使え、見たいときは画面の「画面を映す」で映せます。下のバーの iPhone の形のボタンで、いつでも映す・やめるを切り替えられます。
- iPhone がロックされているときは、利用者が解除します（AI はパスコードを入力しません）。画面が消えているときは、タッチでは点かないのでホームボタン（`ictrl_button home`）で点けます。

## 使い方

- `iCtrl.cmd`（またはショートカット）で iCtrl のウィンドウが開きます。もう開いているときは、そのウィンドウが前に出ます。
- 「USB で接続」または「Wi-Fi で接続」を押します。接続と切断、USB と Wi-Fi の切り替えは、画面上部のバーでいつでもできます（接続中に切り替えると、その方法でつなぎ直します）。押した方法で iPhone が見つからないとき（USB ならケーブルがつながっていない、Apple のサービスが動いていないなど）は、接続も表示も変えずに知らせます。端末の選択やそのほかの設定は、右上のボタンで開くメニューにあります。メニューの外をクリックすると閉じます。
- **使っていないときは iPhone との接続を自動で切ります**（悪用を防ぎ、裏で通信を続けないため。メニュー「使っていなくても接続を続ける（非推奨）」をオンにすると切らない。既定はオフ）。切るのは、AI が操作の終わりを伝えたとき（`ictrl_finish`、Claude Code の応答終了フック。AI のブラウザーで画面を開いていても切り、アプリのウィンドウが見えているときだけ切らない）、人の操作（iCtrl の画面からのタップ・キー・ボタンなど）も AI の操作もないまま 5 分たったとき（画面を開いていても）、iCtrl の画面が見えなくなって（閉じた・最小化した・裏のタブになった）30 秒たったときです。切断すると、トンネル・画面の映像・クリップボードや記録の見張りもすべて止まります。iCtrl のサーバー自体は PC の中からの接続だけを受け付けるので、残っていても iPhone とは通信しません。次に使うときは接続し直してください（自動では接続しません）。

| 操作 | 方法 |
|---|---|
| タップ / ドラッグ | 画面を左クリック / ドラッグ |
| スクロール | マウスホイール（指のドラッグに変換。指はホイールの量だけなめらかに動き、止まってから離すので、iOS の慣性で流れず、1 ノッチごとに同じだけ進みます。画面の端まで来たら指を置き直します。ウィンドウが隠れたら指をすぐ離します） |
| ホームに戻る | 画面下端から上へドラッグ、または Ctrl+H / ドックのホーム |
| アプリ切替 | 下端から上へドラッグして止める、またはドックのボタン |
| ロック | ドックの錠前ボタン。iPhone をロックする（すでにロック中なら何もせず、画面を点けない） |
| 文字入力 | キーボードを接続して PC のキーで入力（日本語は iPhone 側の入力で変換）。PC の文字を入れるときは Ctrl+V か右クリックの「貼り付け」 |
| キーボードの接続 | 起動時からオン（Ctrl+K で切替）。PC のキー操作を iPhone のハードウェアキーボードとして送る |
| 日本語 / 英字入力 | Windows のタスクバーの「あ / A」と連動。Windows で切り替える（半角/全角キーやタスクバーの表示）と iPhone も切り替わる |
| 右クリック | Windows 風メニュー：コピー / 切り取り / 貼り付け / すべて選択 / 元に戻す / やり直し / ここを長押し / 単語を選択 / 画面を PC にコピー / ホーム |
| クリップボード | iPhone でコピーしたテキスト・画像は PC に自動で入る（オフ可）。書式付きテキストは HTML 付きで PC に入る。PC のクリップボードは、iCtrl で貼り付けたとき（Ctrl+V や右クリックの「貼り付け」）だけ iPhone に渡す。メニューに履歴（画像はサムネイル付き）、クリックで PC / iPhone へ再コピー |
| ショートカット | キーボードの接続中の Ctrl+A/C/X/Z/F など → iPhone の ⌘ 操作。Ctrl+V は PC のクリップボードを貼り付け、Ctrl+C/X は iPhone でコピーした文字を PC にも反映 |
| スクリーンショット | Ctrl+S かドックのカメラボタン。既定では ピクチャ › iCtrl（OneDrive などに移したピクチャでもその中）に `iPhone 日付 時刻.png` で保存し、クリップボードにもコピー。撮ると画面が白く光り、「フォルダーを開く」でエクスプローラーが前面に開いて撮ったファイルを選んだ状態になる。保存先はメニュー「スクリーンショット」の「変更…」で選べる（AI からは変更できない） |
| 横向き表示 | iPhone の画面の向き（縦・横）に合わせて自動で回転（クリック位置も合わせて変換）。向きは iOS が画面を回したときの記録（SpringBoard のログ）と、接続時の画面情報で知る |
| パネル表示切替 | Ctrl+B |

### キーボード入力のしくみ

- iOS の入力モード切替には、Apple の JIS キーボードの「かな」（HID LANG1）／「英数」（LANG2）キーを使います（iOS 27 で動作確認）。
- キーボードの接続中は見えない入力欄にフォーカスを置き、Windows の日本語入力を普段どおり有効にしておきます。Windows 側で変換された文字は使わず捨て、変換は iPhone 側で行います。打っている間、Windows の予測変換の窓が画面の左上に出ます（仕様）。Windows の日本語入力を有効にしておく（タスクバーの あ／A を iPhone に合わせる）ためで、ブラウザーの中のページからは予測変換を止められません。気になる場合は、Windows の日本語入力の設定で予測入力をオフにしてください。
- **iPhone の入力モード（かな／英字）は、Windows の IME（タスクバーの あ／A）にそのまま合わせます。** どのアプリが前面でも同じで、iCtrl 独自の入力モードは持ちません。監視（定期的な読み取り）はせず、Windows 自身の知らせで動きます: 日本語入力の仕組み（TSF）が IME の状態が変わるたびに出す WinEvent（`CicMarshalWndClass` の `0x7FFFFF30`。半角/全角の約 3ms 後に出て、その時点で新しい状態が読める）と、前面のウィンドウの切り替えを、サーバーが受け取って IME を読みます（`ictrl/imewatch.py`。Windows は受け取り側を待たないので、キー入力を遅らせません）。サーバーが iPhone の「かな」／「英数」キーを送ります。下のバーの「A / あ」は Windows の IME を切り替えます（iPhone はそれに合わせて変わる）。メニューの「下のバーに「A / あ」を表示」で表示できます（既定は非表示）。一定の間隔で読み取る方式では、切り替えがその間隔だけ遅れます。
- 記号の位置は iPhone 側のハードウェアキーボード設定に従います。JIS キーボードの PC では、iPhone の 設定 > 一般 > キーボード > ハードウェアキーボード を「日本語 - ローマ字」（JIS 配列）にすると、キートップどおりに入力できます。
- 日本語変換は iPhone 側の変換機能で行います（スペースで変換、Enter で確定）。

## AI から操作する（MCP）

```powershell
# Claude Code（全プロジェクトで使う場合）
powershell -ExecutionPolicy Bypass -File setup.ps1 -Claude
# Codex
powershell -ExecutionPolicy Bypass -File setup.ps1 -Codex
```

GUI のメニューの「AI から操作」からも設定をコピーできます。サーバーが動いていなければ MCP サーバーが自動で起動します。

### Claude Code のプラグインとして入れる

```
/plugin marketplace add <このリポジトリの URL>
/plugin install ictrl@ictrl
```

MCP サーバーと、応答が終わったら WDA を止めるフックがまとめて入ります。`setup.ps1 -Claude` と同じ内容なので、どちらか一方だけにしてください（両方入れるとツールが二重に出ます）。

- **Python を入れる必要はありません。** 最初に起動したとき、[uv](https://docs.astral.sh/uv/)（Astral の Python 環境ツール）が Python 3.14 と依存パッケージをプラグインのフォルダーの `.venv` に用意します（約 200MB、20 秒ほど）。
- **uv が入っていればそれを使います**（`winget install astral-sh.uv`）。入っていなければ、版を固定した uv の公式リリース（GitHub、約 15MB）を取得し、SHA256 を照合してからプラグインのフォルダーの `.uv\` に置きます。この場合 Python と uv のキャッシュも `.uv\` に置きます。
- 依存パッケージは `requirements.txt` の版とハッシュのとおりにしか入れません（`--require-hashes`、ビルド済みのファイルのみ）。Python 本体は uv が自身に組み込まれたハッシュで照合します。
- PATH・レジストリ・ほかの Python には触れません。プラグインを外すと、`.venv` と `.uv\` もフォルダーごと消えます（設定・ペアリング記録・ログは `%APPDATA%\iCtrl` と `%LOCALAPPDATA%\iCtrl` に残ります）。
- 初回の準備が MCP の起動待ちに間に合わなかったときは、`/mcp` で ictrl を再接続してください（途中まで取得したものは再利用します）。
- Codex のプラグインとしても入れられます（`codex plugin marketplace add hinatamaxxx/ictrl`、`codex plugin add ictrl@ictrl`。定義は `.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json`、MCP の設定は `plugin/codex-mcp.json`）。Codex のデスクトップアプリはパッケージアプリで、AppData への書き込みが振り替えられます。そのため、パッケージの中から準備するときは、Python もプラグインのフォルダーの `.uv\python` に入れます（パッケージの外で動く iCtrl のサーバーからも見えるように）。

主なツール: `ictrl_connect`, `ictrl_screenshot`, `ictrl_elements`, `ictrl_tap_element`, `ictrl_wait_for`, `ictrl_scroll_to`, `ictrl_alert`, `ictrl_open_url`, `ictrl_terminate_app`, `ictrl_pinch`, `ictrl_rotate`, `ictrl_orientation`, `ictrl_screenshot_marks`, `ictrl_tap_mark`, `ictrl_tap`, `ictrl_swipe`, `ictrl_type_text`, `ictrl_key`, `ictrl_button`, `ictrl_list_apps`, `ictrl_launch_app`, `ictrl_clipboard`, `ictrl_open_window`, `ictrl_wda_ipa`。タップ座標は直前のスクリーンショット画像のピクセルで指定できます。

Claude デスクトップアプリの内蔵ブラウザでは `http://127.0.0.1:47800/` を開くと GUI を使えます（`python -m ictrl url` で URL を表示）。

### 画面の要素で操作する（WebDriverAgent）

`ictrl_elements` は画面上のボタン・アイコン・文字などの種類とラベルを画像なしで返し、`ictrl_tap_element` はラベルを指定してタップします（座標もスクリーンショットも不要）。iOS が要素の位置を渡すのは UI テスト用のアプリだけなので、iPhone に WebDriverAgent（WDA、Appium 製、BSD-3）を入れておきます。Mac も Xcode も不要です。

MCP の操作前後の確認は `ictrl_elements` を優先します。画像や配置を確かめたい場合、または要素取得が使えない場合に `ictrl_screenshot` を使うことで、画像の転送とトークン消費を減らせます。

```powershell
.venv\Scripts\python.exe -m ictrl wda-ipa            # Appium の最新リリースから未署名 IPA を作る
.venv\Scripts\python.exe -m ictrl wda-ipa --bundle-id <既存のApp ID> --out WDA.ipa
```

プラグイン版では、MCP ツール `ictrl_wda_ipa` が同じ処理でデスクトップに `iCtrl-WDA.ipa` を作ります（`bundle_id` も指定できます）。

IPA の中身は Appium の公式リリース（`WebDriverAgentRunner-Runner.zip`）のファイルそのままで、変えるのは Info.plist のバンドル ID と表示名だけです。Apple の XCTest などの部品は含みません（Appium の配布物にも含まれていません）。WebDriverAgent は BSD-3-Clause なので、著作権表示とライセンス文を付ければ再配布できます。このリポジトリの Releases（タグ `wda`）に、未署名の `iCtrl-WDA.ipa` とライセンス文 `WebDriverAgent-LICENSE.txt` を置いています（`tools/release_wda.py` が Appium の公式リリースを取得し、GitHub の記載の SHA256 と照合してから作ります。タグは固定で、Appium の新しい版が出たら中身を差し替えます）。署名は利用者が自分の Apple ID で行います（AltStore Classic、Sideloadly など）。

1. 上のコマンドで `WebDriverAgent-unsigned.ipa` を作る（Appium の GitHub リリースの実機用 `WebDriverAgentRunner-Runner.zip` をそのまま使い、バンドル ID と表示名だけ変える。ダウンロード済みの zip は `--zip` で指定）。
2. AltStore や Sideloadly などで署名して iPhone に入れる。無料の Apple ID は App ID を 7 日に 10 個までしか作れないので、足りなければ `--bundle-id` に登録済みのアプリと同じ ID を指定する（そのアプリは WDA に置き換わり、データは消える）。

WDA が使う XCTest は iPhone にマウントされる開発者ディスクイメージ（iOS 17 以降）から読み込ませるので、Xcode の部品を IPA に入れる必要はありません（iOS 27 で確認）。Apple の部品を含まないので、この IPA は配布もしやすい形です。

WDA を使うツール:

| ツール | 内容 |
|---|---|
| `ictrl_elements` | 要素の種類・ラベル・位置の一覧。ロック中・アラート・前面のシート（その中の要素だけ表示）・横向きも知らせる |
| `ictrl_tap_element` | ラベルでタップ（`texts` で複数を順に） |
| `ictrl_wait_for` | 要素が現れる／消えるまで待つ（上限 60 秒） |
| `ictrl_scroll_to` | 要素が見えるまでスクロール（端で止まる） |
| `ictrl_alert` | アラートの内容とボタンを読む、押す（システムの許可確認も） |
| `ictrl_open_url` | URL・URL スキームを開く |
| `ictrl_terminate_app` | アプリを終了 |
| `ictrl_pinch` / `ictrl_rotate` | 二本指のピンチ・回転 |
| `ictrl_orientation` | 画面の向きを読む・変える（横向きでも座標は自動で変換） |
| `ictrl_screenshot_marks` / `ictrl_tap_mark` | 要素に番号を描いたスクリーンショットと、番号でのタップ |

`ictrl_tap` は `count: 2` でダブルタップ。

Safari の JavaScript ダイアログ（alert / confirm / prompt）が出ている間は、Web ページが iOS のアクセシビリティ問い合わせに応えず、WDA の読み取りがどれも約 10 秒（アラート操作は約 20 秒）待たされます。そこで Safari が前面のときは、まず浅い階層（12 段）だけを読んで（約 0.1 秒）ダイアログの枠を見つけ、その中の文字とボタンを映像から Windows の文字認識（OCR、日本語対応）で読み取ります。読み取り・ボタン操作とも 0.3〜0.7 秒です。

iCtrl は実行ファイル名 `WebDriverAgentRunner-Runner` のアプリを自動で探し、AI が要素の機能を初めて使ったときだけ UI テストとして起動します（約 2〜3 秒）。iPhone との接続を切ると WDA も終了し、接続が突然切れて残った WDA は次の接続時に終了させます。起動時に iPhone が「XCTest を使うために iPhone のパスコードを入力」を求める場合は、下記の自動入力を事前に有効にしておくと iCtrl が対応します。無効の場合は手動入力が必要です。

WDA が動いている間、iPhone 本体の画面には iOS の「Automation Running — Hold both volume buttons to stop」が出ます。iOS 自身の表示なので、アプリから日本語にしたり隠したりはできず、画面共有やスクリーンショットにも写りません。iCtrl のウィンドウの「AI が操作中」の表示が、WDA が動いている間は ✕ 付きで出続け、押すと WDA を終了してこの表示を消します（メニューの「自動操作を終了」も同じで、ホーム画面から手で開いてしまった WDA も終了できます）。iCtrl がない所では、音量ボタンを両方長押しすると止まります。

AI が操作を終えたら、MCP ツール `ictrl_finish` で WDA を終了します。AI のセッション（MCP サーバー）が終わったときも自動で終了します。時間で止めることはしません（WDA を起動し直すたびに iOS がパスコードを求めることがあるため）。

Claude Code の応答が終わるたびに WDA を止めます。`setup.ps1 -Claude` が MCP サーバーと一緒に、Stop フック `python -m ictrl stop-hook` を `~/.claude/settings.json` に登録します（既にある設定は残し、二重には登録しません。手動なら `python -m ictrl claude-hook`）。iCtrl のツールを使ったセッションの応答が終わったときだけ「自動操作を終了」と同じ処理をし、iCtrl を使っていないセッションでは何もしません。

#### WDA の動かし方（メニュー「画面要素の読み取り」）

| 設定 | 動き |
|---|---|
| AI が使うときだけ起動（既定） | 要素を読むときに起動し、`ictrl_finish`・AI の応答終了（Stop フック）・AI の終了・切断で必ず止める |
| 使わない（文字認識のみ） | WDA を起動せず、`ictrl_elements` / `ictrl_tap_element` / `ictrl_wait_for` を Windows の文字認識（OCR）で動かす。結果に `"source": "ocr"` が付く |

WDA を動かしたままにする「常駐」モードはありません。WDA の HTTP（ポート 8100）は認証がなく、iPhone のすべてのネットワーク（同じ Wi-Fi、モバイル通信側など）で接続を受け付けるため、作業が終わるたびに止めます。

WDA が起動できなかったときも、要素の読み取りは OCR で返します（結果に `wda_error` として理由が付きます）。OCR では文字のないアイコンは読めないので、スクリーンショットで位置を確認します。WDA を起動するたびに、前回止めてからの経過時間をログに残します（パスコード要求が出る条件の調査用）。

ホーム画面の WDA アイコン（`ictrl wda-ipa` で作ると名前は「iCtrl WDA」）は開く必要がありません。開くと黒い画面のまま自動操作が始まるので、iOS 18 以降ならアイコンを長押し →「Face IDを必要にする」→「非表示にしてFace IDを必要にする」で、ホーム画面から隠しておけます（機種によっては「パスコードを必要にする」と表示されます）（iCtrl からの起動には影響しません）。

ロック画面やパスコード入力画面が出ている間は「ロック中」として、要素一覧を空にし、名前・番号でのタップとスクロール検索を拒否します（WDA 自身のロック判定は画面が消えている間しか正しくないため、画面上の鍵アイコンとパスコード欄も見ます）。シートやポップオーバーが前面にある間は、その裏に隠れた要素を一覧から外します。

通信は iCtrl のトンネル経由なので USB でも Wi-Fi でも同じです。一覧の取得は 0.1〜2 秒、タップは iCtrl のタッチで送ります。iPhone がロック中だと WDA は起動直後に終了するので、ロックを解除してから使います。

### WDA 起動時のパスコード自動入力

1. iPhone に接続し、メニュー「WDA のパスコード入力」でパスコードを保存します。
2. 「この iPhone の XCTest 要求に自動入力」をオンにします。設定はその iPhone にだけ適用されます。
3. 次に AI が要素一覧を使って WDA を起動すると、XCTest のパスコード要求を Accessibility Audit の画面要素で確認した場合に、iCtrl が保存済みのパスコードを 1 回だけ入力します。

Windows Hello の確認は不要です。自動入力は初期状態でオフです。設定は再起動後も保持され、GUI からいつでもオフにできます。パスコードの保存・削除では自動入力の設定が無効になるため、必要なら改めてオンにしてください。

- 自動入力は WDA の起動中に出る XCTest の要求だけが対象です。iPhone 本体のロック解除、「Face IDを必要にする」でロックしたアプリ（iOS 18 以降）、アプリ独自の PIN、新しいパスコードの設定には入力しません。iPhone 本体は解除してから WDA を使ってください。
- 通常時も通話中も、スクリーンショットや OCR を使わずに要求を検出します。XCTest の起動通知を受けてから、Accessibility Audit の応答に従って画面要素を読み、入力直前にも新しく読み直します。確認は WDA の起動待ち中だけで、起動完了・失敗・切断時に終了します。要素を読み取れない場合は入力せず、画像判定へ切り替えることもありません。
- 要素の見出しの文言とキャンセル・副題、入力欄の「入力済み0個」を確認した時点で読み取りを終えます。検査用の移動する枠は非表示にします。入力欄の判定は iOS 27 の日本語表示で確認しており、別の言語・文言で空欄を確認できない場合は入力しません。パスコードはハードウェアキーボードとして送るため、画面サイズや数字キーの位置に依存しません。
- WDA の起動成功を確認して、次の XCTest 要求に備えます。1 回入力しても起動成功を確認できない場合は、自動入力を停止します。結果を確認してから GUI の「確認して再有効化」で再開してください。アプリの再起動でも停止状態を保持し、再入力しません。
- AI にパスコードを渡したり、AI 用のパスコード入力・設定変更ツールを追加したりはしません。有効化は iCtrl ウィンドウから行います。
- 自動入力の分岐と再送防止はテストで確認しています。iOS 27.0.1 / Wi-Fi の実機でも、XCTest 要求への自動入力から WDA 起動・要素一覧の取得まで確認しています。
- 通話中も、スクリーンショットを使わずに XCTest 要求の検知・自動入力から WDA の要素取得まで実機で確認しています（iOS 27.0.1 / Wi-Fi）。

### XCTest の要求に手動で入力する

XCTest のパスコード要求が出ている場合は、メニューの「パスコードを入力」からも実行できます。Accessibility Audit で XCTest 入力画面を確認してから入力し、判別できない場合は送信しません。確認ダイアログや Windows Hello は不要です。

- 保存・削除・手動入力・自動入力の設定変更は GUI から行います。API は同じ Windows ユーザーからの接続を確認し、Origin とブラウザの識別情報で他サイトや通常の MCP リクエストを拒否します。これらの MCP ツールはありません。同じ Windows ユーザーで動くプログラムに対する認証境界ではなく、そのユーザーからは保存した資格情報も読み取れます。
- 準備中に切断・別の入力・通話による映像停止状態の変更があった場合は停止します。パスコードは1回だけ送り、失敗しても再試行しません。
- パスコードは Windows 資格情報マネージャー（この Windows ユーザーだけが読める）に保存し、ログ・設定・API の応答には出しません。
- 英数字 4〜32 文字に対応します。入力はハードウェアキーボードとして送り、クリップボードは使いません。

## REST API

`http://127.0.0.1:47800/api/...`（ローカルからのみ。他サイトからのリクエストは Origin で拒否）

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/status` | 接続状態・映像統計 |
| GET | `/api/devices` | 見えている iPhone と接続方式 |
| POST | `/api/connect` | `{"transport":"usb"|"wifi","udid"?}` |
| POST | `/api/disconnect` | 切断 |
| GET | `/api/screenshot?format=jpeg|png&max=1024` | 現在の画面 |
| POST | `/api/tap` | `{"x","y"}`（0〜1、または `width`/`height` 付きピクセル） |
| POST | `/api/swipe` | `{"x1","y1","x2","y2","duration"}` |
| POST | `/api/button` | `{"name":"home|app-switcher|lock|volume-up|volume-down|mute|siri"}` |
| POST | `/api/text` | `{"text"}` |
| POST | `/api/key` | `{"key":"enter","modifiers":["cmd"]}` |
| GET | `/api/apps` / POST `/api/launch` | アプリ一覧 / `{"bundle_id"}` で起動 |
| GET/POST | `/api/clipboard` | iPhone のクリップボード（テキスト） |
| POST | `/api/video/start` | 通話中に映像なしで接続したセッションの映像を開始 |
| GET | `/api/elements` | 画面の要素（種類・ラベル・中心座標 0〜1）。WebDriverAgent が必要 |
| GET | `/api/passcode` | 保存有無・資格情報保存機能の利用可否・`automatic`（`enabled`、`blocked`、`device_matches`）。パスコード自体は返さない |
| POST | `/api/passcode/enter` | GUI 専用。新しく取得した静止画で空の XCTest 入力画面を確認し、保存済みパスコードを1回入力 |
| POST | `/api/passcode/automatic` | GUI 専用。`{"enabled":true\|false}`。接続中の iPhone の XCTest 要求への自動入力を設定。有効化には保存済みパスコードが必要 |
| POST | `/api/tap_element` | `{"text"(または配列),"index"?}` ラベルが一致する要素をタップ |
| POST | `/api/wait_for` | `{"text","gone"?,"timeout"?}` |
| POST | `/api/scroll_to` | `{"text","direction"?,"max"?}` |
| GET/POST | `/api/alert` | アラートの内容 / `{"action":"accept"|"dismiss","name"?}` |
| POST | `/api/open_url` / `/api/terminate` | `{"url"}` / `{"bundle_id"}` |
| POST | `/api/pinch` / `/api/rotate` | `{"scale"}` / `{"degrees"}` |
| GET/POST | `/api/orientation` | 向き / `{"orientation":"PORTRAIT"|"LANDSCAPE"}` |
| GET | `/api/screenshot_marks` / POST `/api/tap_mark` | 番号付きスクリーンショット / `{"n"}` |

## フォルダーの構成

| 場所 | 中身 |
|---|---|
| `ictrl/` | iCtrl 本体（Python）。サーバー、iPhone との通信、MCP サーバー |
| `ictrl/ui/` | 画面（HTML・CSS・JavaScript） |
| `window/` | アプリとして使うときのウィンドウ（WebView2 の C# と、Microsoft の WebView2 SDK の DLL） |
| `plugin/` | Claude Code のプラグインの入口（`ictrl.cmd`、`launch.py`）と、応答終了時のフック（`hooks.json`） |
| `.claude-plugin/` | プラグインとマーケットプレイスの定義 |
| `wheels/` | PyPI にソースしかない依存の、ビルド済みのファイル |
| `tests/` | テスト |
| `tools/` | 配布物を作る道具（wheel のビルド、`requirements.txt` のハッシュ、WDA の IPA のリリース） |
| `docs/` | この技術資料。`docs/licenses/` に同梱物のライセンス文 |
| `setup.ps1` / `uninstall.ps1` / `iCtrl.cmd` | アプリとして使うときのセットアップ、アンインストール、起動 |
| `requirements.txt` / `pyproject.toml` | 依存パッケージ（版とハッシュを固定）と Python パッケージの定義 |
| `AGENTS.md` | AI エージェント向けの導入手順（Claude Code も Codex もこのファイルを読む） |

## 開発

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m ictrl serve    # サーバーを前面で起動
```

ログ: `%LOCALAPPDATA%\iCtrl\logs\ictrl.log`

### クリップボード連携のしくみ

- iPhone のコピーは、iPhone が出す `com.apple.pasteboard.notify.changed` 通知で知ります（定期確認はしません）。通知チャネルが切れたら 3 回まで張り直し、それでも駄目なら監視を止めてメニューに「監視停止」と出します。セッション終了で必ず止まります。
- メニューの「iPhone でコピーしたら PC に自動で反映」をオフにすると、iPhone のクリップボード通知の監視と自動取得を停止します。設定は次の接続にも適用されます。iPhone 側の「キーボードが接続されたとき」オートメーションの有効・無効は、ショートカット アプリ側で管理します。
- PC のクリップボードへはサーバーが Win32 API で書き込むので、iCtrl ウィンドウが裏にあっても反映されます。画像は CF_DIB と PNG 形式の両方で入れるので、ほとんどのアプリに貼り付けられます。HEIC の画像も PNG に変換します。
- iCtrl 自身が iPhone に書いた内容（貼り付けで渡した PC の文字など）は、PC 側に戻しません。
- 多くのアプリ（Safari / WebKit、Gmail など）は、コピー時に文字を「後で渡す」形でしか置きません。これを iPhone のクリップボード機能から直接取り出すと、Gmail では約 64 秒かかります（Gmail が作れない旧 Web アーカイブ形式を待つため）。
- そのため iCtrl は **iPhone のショートカット「ictrlcopy」** を使います。クリップボードを取得 → テキストを取得 → （テキストがあれば）クリップボードにコピー、とプレーンテキストに置き換えます。初期設定のキーボード接続方式では、ショートカットの出力は約 0.05 秒で読め、Gmail のコピーは約 1 秒で PC に届きます。
- ictrlcopy は、iCtrl の仮想キーボードの接続を合図に動きます（iPhone 側で ictrlcopy の「キーボードが接続されたとき」をオン）。前面のアプリは切り替わりません（URL で呼び出すと、ショートカット アプリに切り替わってしまいます）。
- ショートカットが 5 秒以内に反応しないときは、iCtrl の画面に作り方を表示します。作り方は次のとおりです。
  1. ショートカット アプリ → ＋ → 編集
  2. 「クリップボードを取得」→「入力からテキストを取得」→「if 文」（テキスト／任意の値）を追加し、「クリップボードにコピー」を「の場合」の中へ
  3. オートメーション →「キーボード」（接続されたとき）、通知オフ
  4. 名前を「ictrlcopy」にして保存
- 「ictrlcopy」の編集画面で、情報 → プライバシー →「ロック中に実行を許可」をオフにすることをおすすめします。ロック中のキーボード接続で、クリップボード処理が動くのを防ぎます。
- AI が iCtrl で iPhone を操作して ictrlcopy を作るときの手順（iOS 27.0.1 で、Claude が WDA の要素一覧を使って最後まで作れることを確認）:
  1. ショートカット アプリの「新規ショートカット」を押すと、最初は「ショートカットを説明」の画面になる。右上の「編集」（読み上げ名は「エディタ」）で、手で組み立てる画面にする。
  2. 下の検索欄にアクションの名前を入れ、結果のセルを押すと末尾に追加される。「クリップボードを取得」「入力からテキストを取得」「if文」の順に足す。
  3. 「if文」の条件は「が次と等しい」で入るので、押して「任意の値」を選ぶ（このボタンの名前には見えない文字が入っていて、名前では押せない。要素一覧の位置で押す）。対象は自動で「テキスト」になる。
  4. 「クリップボードにコピー」は末尾（If文の終了の後）に入る。並べ替えのドラッグは長押しが要るので、そのアクションの左のアイコン（アクションのオプション）→「コピー」で写し、末尾のものは ✕ で消し、「その他の場合」のアクションのオプション →「上にペースト」で「の場合」の中に入れる。
  5. 貼り付けたアクションのコピーする対象は「"if文" の結果」（使えない変数）になるので、押して「変数を消去」し、もう一度押して「テキスト」を選ぶ。
  6. 検索欄に「キーボード」と入れて「キーボード」（"キーボードが接続されたとき"）を押すと、起動条件として先頭に入る（iOS 27 では、オートメーションもショートカットとして保存される）。条件は「接続された」のままにする。
  7. 上のタイトル →「名称変更」で「ictrlcopy」にする。
- 補助アプリ（WDA）を使わない設定でも、同じ手順で最後まで作れた（iOS 27.0.1 で確認）。文字認識ではショートカット アプリの文字がほとんど読めないので、毎回スクリーンショットで位置を決める。画面が切り替わる途中やメニューが開く途中は位置がずれるので、動きが落ち着いてからもう一度撮って押す（検索欄は、アクションを足すたびに下へ移る）。名称変更は、入力欄の × で消してから入力し、Enter で確定する。
- iPhone 本体の画面を指で触ると（backboardd のログで、指の接触半径から本物のタッチだと判別）、イジェクトキーで画面キーボードをすぐ出してから仮想キーボードを外します（外すだけだと iOS が約 1.1 秒待ってから出すため）。つないだ直後は必ず画面キーボードが隠れた状態なので、イジェクトは必ず「出す」側に働き、外した後はハードウェアキーボードがないので出たままになります。PC から画面を操作する（クリック・タップ）か文字を打つと、仮想キーボードをつなぎ直し、iOS は画面キーボードを隠します。イジェクトは切り替えで現在の状態を読み取れないため、それ以外の場面では使いません。
- 仮想キーボードをつなぎ直すとショートカット「ictrlcopy」も動きますが、同じ内容のコピーし直しは通知も PC への書き込みもしません。画面キーボードを出している間は、Windows の あ/A 切替を iPhone に送らず、PC で次に操作するときに反映します。
- PC でコピーしただけでは iPhone に送りません。iCtrl で貼り付けたとき（Ctrl+V・右クリックの「貼り付け」）に、その時の PC のクリップボードを iPhone に渡してから貼り付けます。
- PC で Excel / Word からコピーすると画像も一緒に入るので、貼り付けではテキストがあればテキストを優先して送ります。

### 映像の止まり対策

- iPhone は動きが多いと解像度を変え、新しい設定とキーフレームを続けて送ってきます。デコーダを作り直す間に届いたキーフレームを捨てると、次のキーフレームまで映像が止まります。そのため、作り直しの間のフレームを保持して、後から流します。
- キーフレームが必要な状態で差分フレームが届いたら、ビューアから要求します（0.4 秒に 1 回まで）。検証した端末では、PLI 要求から 0.12〜0.25 秒でキーフレームが届くことを確認済みです。
- 解像度の変更や 1 秒以上フレームが来なかったことは、診断用にログへ記録します（それを理由に再接続などはしません）。

## Windows での通信の落とし穴（AdGuard など）

### AdGuard（Windows 版）の localhost フィルタリング

AdGuard の設定 `filterLocalhost`（localhost のフィルタリング）が有効だと、AdGuard は WFP ドライバー（`adgnetworkwfpdrv`）で、一部の実行ファイルの 127.0.0.1 宛て TCP を自分のプロキシに通します。iPhone の USB 通信（pymobiledevice3 → Apple のサービス、127.0.0.1:27015）もこの経路です（AdGuard のサービスログと詳細設定で確認）。

確かめた症状は次のとおりです。

- 大きな応答が途中で切れる。Python 3.13 では、iCtrl サーバーとの大きめの HTTP 応答がリセットされたり、途中で止まったりしました。同じ要求でも、curl と Store 版の Python 3.12 では正常でした。
- 片側だけ閉じた接続（half-close、`shutdown(SD_SEND)`）が壊れる。送信側を閉じると、相手がまだ開いているのに受信側に EOF が早く届いたり、逆方向の送信が WinError 10053 で失敗したりしました。AdGuard の保護をオン・オフ・オンと切り替えると、失敗・成功・失敗が秒単位でそろって再現しました。AdGuard の公式 Issue #6233 と同じ症状で、ドライバーの版も同じです。
- Windows ごと落ちる（ブルースクリーン）。開発中に起きた 5 回は、すべて NETIO の stream callout の中で止まっていました（0xD1 が 4 回、0x1E が 1 回）。0x1E のダンプでは、AdGuard のドライバー自身が TCP ストリームにデータを再注入する処理（`FwpsStreamInjectAsync0`）から、接続の切断処理の中で落ちていました。最有力の原因は AdGuard の WFP ドライバーですが、シンボルがないので、ドライバー内部のどの不具合かは特定していません。

iCtrl では次のようにしています。

- **MCP とサーバーのあいだ**: localhost の HTTP ではなく、Windows の名前付きパイプで通信します（`ictrl/pipe.py`）。名前付きパイプはネットワーク接続ではないので、ネットワークのフィルターが間に入りません。
- **ブラウザー（GUI）からの HTTP と WebSocket**: この問題は起きていないので、localhost のままです。
- AdGuard の設定は iCtrl から変えません。利用者が除外するなら、詳細設定の `filterLocalhost` をオフにする（localhost には広告がないので保護への影響は小さい）か、アプリの除外に iCtrl の Python（`.venv\Scripts\python.exe` と `pythonw.exe`。プラグイン版では Claude のプラグインのフォルダーの中の `.venv`）と Apple の `AppleMobileDeviceProcess.exe` を入れます。ただし、除外してもドライバー自体は読み込まれたままなので、ブルースクリーンが止まるかは確かめていません。

### pymobiledevice3 のローカル中継（AdGuard とは別の原因）

pymobiledevice3 は Windows では、iPhone のサービスへの接続を 127.0.0.1 の TCP の中継に通し、最初の 2 バイトで宛先のポートを渡します。Wi-Fi でこの 2 バイトが失われ、続く "PRI * HTTP/2.0" の "PR" が宛先のポート（0x5052）として読まれ、iPhone が接続を拒否していました。中継の相手は同じプロセスの中のソケットで、フィルターではありません（Windows の Proactor のソケット対の間で消えていた）。iCtrl はプロセスの中でトンネルの TCP に直接つなぎ、中継を使いません（`ictrl/direct_dial.py`）。

## 既知の制限

- 音声は扱いません（iPhone の音声ストリームは AAC-ELD で、WebCodecs では再生できないため）。
- 日本語入力に [Meltype](https://github.com/yksr-melt/Meltype) を使っていると、英語を打っている最中に Windows の IME が勝手に「あ」に切り替わることがあります（Meltype 側の問題。打っている間に、IME のオン／オフや英数／ひらがなが 1 秒に何回も切り替わる）。iCtrl は Windows の IME に合わせるので、iPhone の入力モードも一緒に切り替わります。Meltype はアプリを閉じても日本語入力（TSF の IME）として動き続けるため、避けるには Windows の日本語入力を Microsoft IME などに切り替えてください（`Windows キー + Space`）。
- PC の文字は貼り付けで渡します。画像など、テキスト以外のクリップボード内容は元に戻せません。
- Wi-Fi は AirPlay を使いません。USB と同じ CoreDevice の画面ストリームを RemotePairing の暗号化トンネル越しに受けるので、whoo などのアプリが AirPlay 用の画面に切り替わることはありません（whoo で確認）。
- 通話中（電話・LINE などの VoIP）や録音中など、iPhone のマイクかカメラが使われている間は、iOS が画面の映像を出しません（CoreDevice エラー 9022 "The device microphone or camera is currently in use"）。その場合は映像なしで接続し、操作・AI（要素の読み取り）・クリップボード・スクリーンショット（Instruments 経由の静止画）を利用します。終わったら画面の「画面を映す」（`POST /api/video/start`）で映像を始めます。自動では再試行しません。
- 逆に、映像を流している間はカメラが使えません（カメラアプリが裏にいる扱いになり、真っ暗になる）。iCtrl は iOS の録画インジケーター（SpringBoard のログ）でカメラの使用を知り、その間だけ映像を止め、カメラが使われなくなったら映像に戻します。
- iOS 27 では、Wi-Fi の lockdown 経由で CoreDevice トンネルを張ろうとすると iPhone 側に切断されます。このため Wi-Fi は `_remotepairing._tcp`（Bonjour）経由だけを使います。

## ライセンス

iCtrl は [GNU General Public License v3.0 以降](../LICENSE)（GPL-3.0-or-later）で公開しています。iPhone との通信に使う [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) が GPL-3.0-or-later のためです。

| 依存 | ライセンス | 入手方法 |
|---|---|---|
| pymobiledevice3 | GPL-3.0-or-later | pip（setup.ps1 が導入） |
| lzfse（`wheels\` に同梱） | BSD-3-Clause（Apple の LZFSE）/ MIT（Python バインディング） | 配布元がビルド（ソースは PyPI の lzfse 0.4.2） |
| pylzss（`wheels\` に同梱） | LGPL-3.0-or-later | 配布元がビルド（ソースは PyPI の pylzss 0.3.4、無改変） |
| hexdump（`wheels\` に同梱） | Public Domain | 配布元がビルド（ソースは PyPI の hexdump 3.3） |
| Pillow / pillow-heif | MIT-CMU / BSD-3-Clause（libheif などを同梱） | pip |
| Starlette / uvicorn / wsproto | BSD-3-Clause / BSD-3-Clause / MIT | pip |
| pywin32 | PSF | pip |
| pywinrt（winrt-*、Windows の OCR を呼ぶ） | MIT | pip |
| WebDriverAgent（任意） | BSD-3-Clause（Appium） | 利用者が `ictrl wda-ipa` で Appium の公式リリースから取得。このリポジトリには含まない |

このリポジトリに含む依存ライブラリのバイナリは、上の `wheels\` の 3 つだけです（PyPI のソースを無改変でビルドしたもの。ライセンス文は各 wheel に同梱）。Apple の SDK・Xcode の部品、IPA は含まれていません。iPhone、iOS、Xcode は Apple Inc. の商標です。iCtrl は Apple Inc. とは関係がなく、Apple による承認や支援を受けていません。

