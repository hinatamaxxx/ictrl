# iCtrl — Windows から iPhone を操作する

日本語 | [English](README.en.md)

iPhone の画面を Windows のパソコンに映して、マウスとキーボードで操作できるアプリです。USB ケーブルでも、同じ Wi-Fi につないだ状態でも使えます。AI の Claude に「iPhone で設定を開いて」のように頼んで、iPhone を操作してもらうこともできます。

Apple とは関係のない非公式のツールです。現状のまま公開しており、不具合の報告、質問、要望は受け付けていません（[LICENSE](LICENSE) の免責のとおり、自己責任で使ってください）。

> **English summary:** iCtrl mirrors and controls an iPhone from Windows over USB or Wi-Fi on the same local network, and lets Claude Code / Codex drive the same iPhone through MCP. Windows only. Unofficial; not affiliated with Apple. Provided as is: no support, bug reports or feature requests are accepted.

## できること

- iPhone の画面をパソコンに映す（音は出ません）
- クリックでタップ、マウスホイールでスクロール、キーボードで文字を入力する（日本語も入力できます）
- iPhone でコピーした文字や画像をパソコンで貼り付ける。パソコンの文字を iPhone に貼り付ける
- iPhone の画面をパソコンにスクリーンショットとして保存する
- Claude に頼んで、iPhone を操作してもらう

## 用意するもの

| もの | 説明 |
|---|---|
| Windows 10 か 11 のパソコン | Mac では使えません |
| Claude のデスクトップアプリ | Claude のプラグインとして使う場合に必要です。Claude Code（アプリの「Code」タブ）を使います |
| Apple Devices アプリ | パソコンと iPhone を USB でつなぐために必要です。[Microsoft Store](https://apps.microsoft.com/detail/9np83lwlpz9k) から無料で入れられます（iTunes が入っていれば不要です） |
| iPhone | iOS 17.4 以降。**デベロッパモード** をオンにしておきます（設定 › プライバシーとセキュリティ › デベロッパモード。オンにすると iPhone が再起動します） |
| USB ケーブル | 最初の 1 回だけ使います。2 回目からは Wi-Fi でも接続できます |

Claude のプラグインとして使う場合は、Python などのプログラムを自分で入れる必要はありません。iCtrl を最初に起動したときに、必要なものを自動でダウンロードして用意します（約 200MB、20 秒ほど）。アプリとして使う場合は、Python 3.13 以上が必要です（[アプリとして入れる](#アプリとして入れるclaude-を使わない場合) を見てください）。

## 入れ方

入れ方は 2 つあります。Claude からも iPhone を操作したいなら、Claude のプラグインとして入れるのがおすすめです。Claude を使わず、iCtrl だけをアプリとして使うこともできます。

### Claude のプラグインとして入れる（おすすめ）

1. Claude のデスクトップアプリを開き、「Code」タブで新しいセッションを始めます。
2. 次のように頼みます。

   ```
   iCtrl のプラグインを入れて。マーケットプレイスは hinatamaxxx/ictrl、プラグインは ictrl@ictrl。
   ```

   Claude が次の 2 つのコマンドを実行します。自分でターミナルに入力してもかまいません。

   ```bash
   claude plugin marketplace add hinatamaxxx/ictrl
   ```

   ```bash
   claude plugin install ictrl@ictrl
   ```

3. Claude のアプリを再起動します。
4. 新しいセッションで「iCtrl の画面を開いて」と頼みます。アプリの右側のブラウザーに iCtrl の画面が出れば完了です。

このリポジトリが非公開の間は、閲覧できる GitHub アカウントでサインインしているパソコンでだけ入れられます。

### アプリとして入れる（Claude を使わない場合）

スタートメニューから開く、ふつうの Windows のアプリとして入れます。iCtrl の画面は専用のウィンドウで開きます。

1. **Python を入れる。** [python.org](https://www.python.org/downloads/) から最新の Python をダウンロードして入れます。インストーラーの最初の画面で「Add python.exe to PATH」にチェックを入れてください。Microsoft Store の Python は使えません。
2. **iCtrl をダウンロードする。** このリポジトリのページの緑の「Code」ボタンから「Download ZIP」を選び、ダウンロードした ZIP を展開します。展開したフォルダーは、たとえば「ドキュメント」の中の `iCtrl` のような、あとで動かさない場所に置きます（ここがプログラムの置き場所になります）。
3. **セットアップする。** 展開したフォルダーをエクスプローラーで開き、上のアドレスバーに `powershell` と入力して Enter を押します。開いた画面に次の 1 行を貼り付けて Enter を押します。数分かかります。

   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1 -Shortcut
   ```

4. **開く。** スタートメニューの「iCtrl」を開きます。

デスクトップにもアイコンを置くときは、3 の行の最後に ` -Desktop` を足します。Codex から iPhone を操作したいときは ` -Codex` を足します。AI エージェントにこのリポジトリの URL を渡して「導入して」と頼むと、[AGENTS.md](AGENTS.md) の手順でここまでを進めてくれます。詳しくは [技術資料の「セットアップ」](docs/technical.md#セットアップclaude-のプラグインを使わない場合) にあります。

## 初めて iPhone をつなぐ

1. iPhone を USB ケーブルでパソコンにつなぎます。
2. iCtrl の画面で「USB で接続」を押します。
3. iPhone に「このコンピュータを信頼しますか？」と出たら「信頼」を押し、iPhone のパスコードを入力します。
4. iPhone の画面が映れば完了です。

このとき、Wi-Fi で接続するための準備も自動で済みます。2 回目からは、パソコンと iPhone が同じ Wi-Fi にあれば、ケーブルなしで「Wi-Fi で接続」を押すだけで使えます。

## ふだんの使い方

1. iCtrl の画面を開きます。プラグインなら Claude に「iCtrl の画面を開いて」と頼み、アプリならスタートメニューの「iCtrl」を開きます。
2. 画面の上にある「USB」か「Wi-Fi」を選んで接続します。
3. 終わったら「切断」を押すか、画面を閉じます。

| 操作 | やり方 |
|---|---|
| タップ | 画面をクリック |
| スクロール | マウスホイール |
| ホームに戻る | 下のバーの家のボタン、または Ctrl+H |
| アプリの切り替え | 下のバーの切り替えボタン |
| 画面のロック | 下のバーの錠前のボタン |
| 文字の入力 | パソコンのキーボードでそのまま打つ。日本語の変換は iPhone 側で行います（スペースで変換、Enter で確定） |
| 日本語と英字の切り替え | Windows の「あ／A」の切り替え（半角/全角キー）に、iPhone も合わせて切り替わります |
| 貼り付け | Ctrl+V、または右クリックの「貼り付け」 |
| スクリーンショット | Ctrl+S、または下のバーのカメラのボタン。「ピクチャ › iCtrl」に保存されます |
| 画面を映す・止める | 下のバーの iPhone の形のボタン |

iPhone のロックは自分で解除してください。

## Claude に iPhone の操作を頼む

iCtrl を入れた Claude に、たとえば次のように頼みます。

```
iPhone で設定アプリを開いて、Wi-Fi の画面を出して。
```

- Claude が操作している間は、iCtrl の画面の上に「AI が操作中」と表示され、Claude が触った場所に紫の点が出ます。
- 「AI が操作中」の ✕ を押すと、すぐに止められます。マウスで操作すれば、いつでも自分が引き継げます。
- Claude は iPhone のパスコードを入力しません。ロックがかかったら、自分で解除してください。

### 補助アプリ（WebDriverAgent）を入れる

Claude は iPhone の画面にあるボタンや文字を読み取って操作します。iPhone に補助アプリ「iCtrl WDA」（WebDriverAgent）を入れておくと、Claude は画面のボタンや文字の種類と位置を一覧で読み取り、画像を見なくても名前を指定して押せます。入れなくても使えますが、その場合は画面の文字を画像から読み取るので、文字のないアイコンは読めません。

補助アプリは署名していない状態で配布しています。iPhone に入れるときに、自分の Apple ID で署名してください。Apple の仕組みで決まっていることなので、iCtrl では省けません。

1. **ダウンロードする。** [Releases の「iCtrl WDA」](https://github.com/hinatamaxxx/ictrl/releases/tag/wda) から `iCtrl-WDA.ipa` をダウンロードします。
2. **署名して iPhone に入れる。** パソコンに [AltStore Classic](https://altstore.io/)（パソコン側の AltServer と組み合わせて使います）か [Sideloadly](https://sideloadly.io/) を入れ、iPhone を USB でつないで、`iCtrl-WDA.ipa` を自分の Apple ID で入れます（無料の Apple ID でかまいません）。
3. **iPhone で信頼する。** iPhone の 設定 › 一般 › VPN とデバイス管理 で、自分の Apple ID を「信頼」します。
4. Claude に何か操作を頼むと、iCtrl が補助アプリを自動で使います。補助アプリをホーム画面から開く必要はありません。

無料の Apple ID で入れた補助アプリは、7 日で使えなくなります。そのときは、もう一度 2 の手順で入れ直してください（AltStore Classic なら更新の操作だけで済みます）。

補助アプリの初回の起動で iPhone がパスコードを求めることがあります。その場合は iPhone で自分で入力するか、iCtrl のメニューの「WDA のパスコード入力」で設定してください。

`iCtrl-WDA.ipa` は、Appium が公開している WebDriverAgent の配布物から、アプリの ID と表示名だけを変えたものです。WebDriverAgent のライセンス（BSD-3-Clause）の文は、同じ Releases の `WebDriverAgent-LICENSE.txt` にあります。ダウンロードせずに、Claude に「iCtrl の補助アプリの IPA を作って」と頼んで、パソコンの上で同じファイルを作ることもできます（デスクトップにできます）。

## コピーを速くするショートカット

iPhone でコピーした文字をパソコンに速く届けるために、iPhone のショートカット「ictrlcopy」を使います。なくても使えますが、Gmail など一部のアプリでは、コピーしてからパソコンに届くまで 1 分ほどかかります（ictrlcopy があれば約 1 秒）。

補助アプリ（iCtrl WDA）を入れてあれば、ショートカットも Claude に作ってもらえます。Claude が iPhone のショートカット アプリを操作する様子は、iCtrl の画面でそのまま見ていられます。

```
iCtrl で iPhone を操作して、ショートカット「ictrlcopy」を作って。作り方は https://github.com/hinatamaxxx/ictrl/blob/main/docs/technical.md の「クリップボード連携のしくみ」にあります。
```

自分で作るときは、iPhone のショートカット アプリで次のように作ります。

1. 「新規ショートカット」を押し、右上の「編集」を押します。
2. 下の検索欄で探して、「クリップボードを取得」「入力からテキストを取得」「if文」の順に足します。
3. 「if文」の条件を「任意の値」にし、「の場合」の中に「クリップボードにコピー」（コピーするのは「テキスト」）を入れます。
4. 検索欄で「キーボード」を探して足します（「キーボードが接続されたとき」に動くようになります）。
5. 名前を「ictrlcopy」にします。

iPhone がロックされているときに動かないよう、ictrlcopy の設定で「ロック中に実行を許可」をオフにしておくことをおすすめします。

## 安心して使うために

- iCtrl は USB でつないだ iPhone か、同じ Wi-Fi にある iPhone だけを操作します。外出先からは使えません。
- iCtrl を使っていないときは、iPhone との接続を自動で切ります。画面を閉じてから 30 秒たったとき、Claude が操作を終えたとき（画面を閉じている場合）、Claude の最後の操作から 5 分たったときです。自動でつなぎ直すことはありません。
- 接続がなく、画面も開いていない状態が 1 分続くと、iCtrl 自体も終了します。パソコンの起動時に自動で起動することもありません。
- iCtrl は同じパソコンの中からの操作だけを受け付けます。

## うまくいかないとき

| 困っていること | 確かめること |
|---|---|
| USB で接続できない | Apple Devices アプリが入っているか。ケーブルを挿し直して、iPhone で「信頼」を押したか |
| Wi-Fi で接続できない | 一度 USB で接続したか。パソコンと iPhone が同じ Wi-Fi にあるか |
| 「デベロッパモードを有効にしてください」と出る | iPhone の 設定 › プライバシーとセキュリティ › デベロッパモード をオンにする |
| 画面が映らない | 電話や LINE の通話中、録音中は、iPhone が画面を送りません。終わってから画面の「画面を映す」を押してください |
| Claude が iCtrl を使えない | Claude のアプリを再起動する。初めて使うときは準備に 20 秒ほどかかります |
| AdGuard を使っていて、接続が途中で切れる | AdGuard の localhost のフィルタリングが通信を妨げることがあります。詳しくは [技術資料](docs/technical.md#windows-での通信の落とし穴adguard-など) を見てください |
| 英語を打っていると勝手に日本語になる | 日本語入力に Meltype を使っていると起きます。Windows の日本語入力を Microsoft IME などに切り替えてください（Windows キー + Space） |

記録（ログ）は `%LOCALAPPDATA%\iCtrl\logs\ictrl.log` にあります。

## 更新と削除

### Claude のプラグインの場合

- **更新：** Claude に「iCtrl のプラグインを更新して」と頼み、Claude のアプリを再起動します。
- **削除：** Claude に「iCtrl のプラグインを削除して」と頼みます。設定とペアリングの記録はパソコンに残ります。完全に消すときは、`%APPDATA%\iCtrl` と `%LOCALAPPDATA%\iCtrl` の 2 つのフォルダーを削除します。

### アプリの場合

- **更新：** 新しい ZIP を同じフォルダーに上書きして展開し、セットアップの 3 をもう一度行います。
- **削除：** Windows の「設定 › アプリ › インストールされているアプリ」で iCtrl をアンインストールします。設定とデータを残すかどうかを最初に選べます。展開したフォルダーは残るので、要らなければ削除します。

## もっと詳しく

仕組み、REST API、AI 向けのツールの一覧、補助アプリの細かい動き、パスコードの自動入力などは [技術資料](docs/technical.md) にあります。

## ライセンス

iCtrl は [GNU General Public License v3.0 以降](LICENSE)（GPL-3.0-or-later）で公開しています。使っているライブラリのライセンスは [技術資料の「ライセンス」](docs/technical.md#ライセンス) にあります。iPhone、iOS、Xcode は Apple Inc. の商標です。iCtrl は Apple Inc. とは関係がなく、Apple による承認や支援を受けていません。
