# iCtrl の導入手順（AI エージェント向け）

利用者から「<このリポジトリの URL> を導入して」と頼まれたときの手順です。

## AI が守ること

- **UAC（管理者の確認）、パスワード・パスコード、ファイアウォール、外部サービスの管理画面の操作は、利用者本人に任せる。** 必要な場面では、何をどこで押すかを伝えて待つ。
- **iPhone のパスコードは絶対に入力しない。** ロック中・パスコード画面では利用者に解除を頼む。
- 秘密の情報（ペアリング記録、パスコード、トークン）をチャットやリポジトリに出さない。
- iPhone の「信頼しますか？」は利用者が iPhone 上で押す。
- インターネットへの公開は、利用者の了承を得てから。iCtrl は USB か同じネットワークの iPhone だけを対象にする（外出先からの接続には対応しない）。

## 1. 前提の確認

1. Windows 10/11 であること（iCtrl は Windows 専用）。
2. **Python 3.13 以上（python.org 版。最新版でよい）** が入っていること。iCtrl は Python を同梱しない。無ければ利用者に `winget install Python.Python.3.14`（または python.org のインストーラー）での導入を頼む。**Microsoft Store 版は不可**（AppData の書き込みが振り替えられるため）。
3. 画面は WebView2（Windows に標準で入っている）で開く。Chrome は不要。
4. **Apple Devices アプリ**（Microsoft Store。`winget install --id 9NP83LWLPZ9K --source msstore`）か iTunes が入っていること（USB 通信のドライバ。`setup.ps1` が無いと知らせる）。無ければ導入を利用者に頼む。
5. iPhone 側: iOS 17.4 以降、**デベロッパモード** オン（設定 › プライバシーとセキュリティ › デベロッパモード）。利用者に確認してもらう。

## 2. 取得とセットアップ

```powershell
git clone <このリポジトリの URL> iCtrl
cd iCtrl
powershell -ExecutionPolicy Bypass -File setup.ps1 -Shortcut
```

- 管理者権限は不要。仮想環境（約 190MB）をフォルダー内の `.venv` に作る。コンパイラーは不要（ソースしか無い依存は `wheels\` にビルド済みを同梱）。
- スタートメニューに「iCtrl」、「設定 → アプリ → インストールされているアプリ」に iCtrl が登録される。

## 3. 追加の設定（利用者が望むか確かめてから）

基本の導入が終わったら、次のどれを使うか利用者に尋ねる。

| 希望 | すること |
|---|---|
| Claude Code から使う | `powershell -ExecutionPolicy Bypass -File setup.ps1 -Claude`（MCP サーバーと、応答終了時に WDA を止めるフック） |
| Claude Code だけで使い、Python を入れたくない | プラグイン: 利用者に Claude Code で `/plugin marketplace add <このリポジトリの URL>` と `/plugin install ictrl@ictrl` を実行してもらう（初回起動時に uv が Python と依存パッケージを用意する。README「Claude Code のプラグインとして入れる」）。`-Claude` とは併用しない |
| Codex から使う | `powershell -ExecutionPolicy Bypass -File setup.ps1 -Codex` |
| デスクトップにも置く | `powershell -ExecutionPolicy Bypass -File setup.ps1 -Desktop` |
| 画面の要素で操作（WebDriverAgent） | README「補助アプリ（WebDriverAgent）を入れる」の手順。プラグイン版は MCP ツール `ictrl_wda_ipa`、それ以外は `.venv\Scripts\python.exe -m ictrl wda-ipa` で IPA を作り、利用者が Sideloadly や AltStore で署名して iPhone に入れる |

## 4. 確認

1. スタートメニューの「iCtrl」（または `iCtrl.cmd`）でウィンドウが開く。
2. iPhone を USB でつなぎ、「USB で接続」→ iPhone で「信頼」（利用者が押す）。
3. 上部のバーに端末名が出て（接続方式は隣の USB / Wi-Fi ボタンで分かる）、iPhone の画面が映ることを確かめる（既定で接続時に画面を映す）。
4. Claude Code に登録した場合は、新しいセッションで `ictrl_status` が使えることを確かめる。

## 5. うまくいかないとき

- ログ: `%LOCALAPPDATA%\iCtrl\logs\ictrl.log`
- USB の iPhone が見えない: Apple Devices アプリが入っているか、ケーブルを挿し直して iPhone で「信頼」したか。
- Wi-Fi で見えない: 一度 USB で接続したか（Wi-Fi 用のペアリングが自動で作られる）。PC と iPhone が同じネットワークか。
- 依存関係のインストールに失敗: ネットワーク（PyPI に届くか）と Python の版（3.13 以上、python.org 版）を確かめて `setup.ps1` をやり直す。

## 6. アンインストール

「設定 → アプリ → インストールされているアプリ」の iCtrl、または

```powershell
powershell -ExecutionPolicy Bypass -File uninstall.ps1
```

既定では設定とデータも削除する（`-KeepSettings` で残す）。ソースのフォルダーは残るので、不要なら利用者の了承を得て削除する。
