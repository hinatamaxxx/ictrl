# iCtrl — control an iPhone from Windows

[日本語](README.md) | English

iCtrl shows your iPhone's screen on a Windows PC and lets you use it with the mouse and keyboard, over a USB cable or the same Wi-Fi network. You can also ask Claude, the AI assistant, to operate the iPhone for you ("open Settings on the iPhone").

## What it does

- Shows the iPhone's screen on the PC (no sound)
- Click to tap, scroll with the mouse wheel, type with the PC keyboard (Japanese input included)
- Paste text and images copied on the iPhone into the PC, and paste PC text into the iPhone
- Saves screenshots of the iPhone on the PC
- Lets Claude operate the iPhone for you

## What you need

| Item | Notes |
|---|---|
| A Windows 10 or 11 PC | Macs are not supported |
| The Claude desktop app | Needed to use iCtrl as a Claude plugin, from Claude Code (the app's "Code" tab) |
| The Apple Devices app | Needed to connect the iPhone over USB. Free from the [Microsoft Store](https://apps.microsoft.com/detail/9np83lwlpz9k) (not needed if iTunes is installed) |
| An iPhone | iOS 17.4 or later, with **Developer Mode** on (Settings › Privacy & Security › Developer Mode; the iPhone restarts when you turn it on) |
| A USB cable | Only for the first connection. After that, Wi-Fi works too |

As a Claude plugin, you do not need to install Python or anything else yourself: the first time iCtrl starts, it downloads what it needs (about 200 MB, some 20 seconds). As a standalone app, it needs Python 3.13 or later (see [Install as an app](#install-as-an-app-without-claude)).

## Install

There are two ways. To let Claude operate the iPhone too, install iCtrl as a Claude plugin (recommended). You can also use iCtrl alone as an app, without Claude.

### Install as a Claude plugin (recommended)

1. Open the Claude desktop app and start a new session in the "Code" tab.
2. Ask:

   ```
   Install the iCtrl plugin: marketplace hinatamaxxx/ictrl, plugin ictrl@ictrl.
   ```

   Claude runs these two commands. You can also type them in a terminal yourself.

   ```bash
   claude plugin marketplace add hinatamaxxx/ictrl
   ```

   ```bash
   claude plugin install ictrl@ictrl
   ```

3. Restart the Claude app.
4. In a new session, ask "Open the iCtrl screen". When the iCtrl screen appears in the browser on the right side of the app, you are done.

While this repository is private, the plugin installs only on PCs signed in to a GitHub account that can see it.

### Install as an app (without Claude)

This installs iCtrl as an ordinary Windows app that you open from the Start menu. Its screen opens in its own window. The first time only, prepare three things.

1. **Install Python.** iCtrl runs on Python, so install it first.
   1. On [python.org's download page](https://www.python.org/downloads/), press "Download Python" to get the installer.
   2. Open the installer, check "Add python.exe to PATH" at the bottom of the first screen, then press "Install Now".
   3. The Microsoft Store Python does not work (settings and pairing records would end up in a separate place).
2. **Download iCtrl.**
   1. On this repository's page, press the green "Code" button and choose "Download ZIP".
   2. Right-click the downloaded ZIP and choose "Extract All".
   3. Put the extracted folder somewhere it can stay, for example in Documents, and name it `iCtrl`. The program runs from there, so do not move it later.
3. **Set it up.**
   1. Open the `iCtrl` folder in File Explorer.
   2. Click the address bar (where the folder's location is shown), type `powershell` and press Enter. A dark window (PowerShell) opens.
   3. Paste this line and press Enter. It downloads what it needs, which takes a few minutes. It is done when it shows "OK: iCtrl をインストールしました".

   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1 -Shortcut
   ```

This adds "iCtrl" to the Start menu. See [Start and stop](#start-and-stop) for how to open it.

Add ` -Desktop` to the end of the line in step 3 for a desktop icon, or ` -Codex` to operate the iPhone from Codex. Or give an AI agent this repository's URL and ask it to install iCtrl: it follows [AGENTS.md](AGENTS.md). Details are in [the technical notes, "セットアップ"](docs/technical.md#セットアップclaude-のプラグインを使わない場合) (Japanese).

## Connect the iPhone for the first time

1. Connect the iPhone to the PC with a USB cable.
2. On the iCtrl screen, press "USB で接続" (Connect over USB).
3. When the iPhone asks "Trust This Computer?", tap "Trust" and enter the iPhone's passcode.
4. When the iPhone's screen appears, you are done.

This also prepares the Wi-Fi connection. From then on, if the PC and the iPhone are on the same Wi-Fi network, press "Wi-Fi で接続" (Connect over Wi-Fi), with no cable.

## Start and stop

iCtrl is made of a part that runs in the background (the server) and the screen that shows and controls the iPhone. You never need to start or stop the server yourself.

| Used as | Start | Stop |
|---|---|---|
| Claude plugin | Ask Claude to "open the iCtrl screen", or to do something on the iPhone: the server starts on its own. The screen appears in the browser on the right side of the Claude app | It ends on its own once the screen is closed and Claude no longer uses iCtrl |
| App | Open "iCtrl" from the Start menu: the server starts on its own and the iCtrl window opens | It ends on its own when you close the window |

Either way, the server exits by itself after 1 minute with no iPhone connected. While only the server runs, no icon appears on the taskbar or in the notification area. If the iPhone is still connected when you close the iCtrl window, the connection closes after 30 seconds and the server then exits.

## Everyday use

1. Open the iCtrl screen (see [Start and stop](#start-and-stop)).
2. Choose "USB" or "Wi-Fi" at the top of the screen to connect.
3. When you are done, press "切断" (Disconnect) or close the screen.

| Action | How |
|---|---|
| Tap | Click the screen |
| Scroll | Mouse wheel |
| Go home | The house button in the bottom bar, or Ctrl+H |
| Switch apps | The app switcher button in the bottom bar |
| Lock the screen | The padlock button in the bottom bar |
| Type | Type on the PC keyboard. Japanese is converted on the iPhone (Space to convert, Enter to confirm) |
| Japanese / English input | The iPhone follows the Windows "あ / A" input mode (the Hankaku/Zenkaku key) |
| Paste | Ctrl+V, or "Paste" in the right-click menu |
| Screenshot | Ctrl+S, or the camera button in the bottom bar. Saved in Pictures › iCtrl |
| Show or hide the screen | The iPhone-shaped button in the bottom bar |

Unlock the iPhone yourself when it is locked.

## Ask Claude to operate the iPhone

With iCtrl installed, ask Claude, for example:

```
On the iPhone, open the Settings app and show the Wi-Fi screen.
```

- While Claude works, the iCtrl screen shows "AI が操作中" (AI at work) at the top, and a purple dot where Claude touches.
- Press the ✕ on "AI が操作中" to stop it at once. Use the mouse to take over at any time.
- Claude never enters the iPhone's passcode.

### Install the helper app (WebDriverAgent)

Claude operates the iPhone by reading the buttons and text on its screen. With the helper app "iCtrl WDA" (WebDriverAgent) on the iPhone, Claude reads the kind and position of each button and text as a list, and presses them by name without looking at images. iCtrl also works without it (see [How Claude reads the screen](#how-claude-reads-the-screen)).

The helper app is distributed unsigned. Sign it with your own Apple ID when you install it: Apple requires this, and iCtrl cannot skip it.

1. **Download.** Get `iCtrl-WDA.ipa` from [the "iCtrl WDA" release](https://github.com/hinatamaxxx/ictrl/releases/tag/wda).
2. **Sign and install.** Install [AltStore Classic](https://altstore.io/) (used together with AltServer on the PC) or [Sideloadly](https://sideloadly.io/) on the PC, connect the iPhone over USB, and install `iCtrl-WDA.ipa` with your own Apple ID (a free Apple ID is fine).
3. **Trust it on the iPhone.** In Settings › General › VPN & Device Management, trust your Apple ID.
4. When you ask Claude to do something, iCtrl uses the helper app on its own. You do not need to open it from the Home Screen.

With a free Apple ID, the helper app stops working after 7 days. Install it again with step 2 (AltStore Classic only needs a refresh).

The first time the helper app starts, the iPhone may ask for its passcode. Enter it on the iPhone yourself, or set it up in iCtrl's menu, "WDA のパスコード入力" (Passcode for WDA).

`iCtrl-WDA.ipa` is Appium's published WebDriverAgent build with only the app ID and the display name changed. The WebDriverAgent license (BSD-3-Clause) is `WebDriverAgent-LICENSE.txt` in the same release. Instead of downloading it, you can also ask Claude to "make the iCtrl helper app IPA": it builds the same file on the PC, on the desktop.

### How iCtrl WDA differs from other WebDriverAgent builds

WebDriverAgent (WDA) is an app widely used by iPhone test automation tools such as Appium. iCtrl WDA is Appium's official build prepared for iCtrl, and it works the same. The differences:

- **Name and ID**: its Home Screen name is "iCtrl WDA" and its app ID is `com.ictrl.wda`. The program inside is not rebuilt; it is Appium's as published.
- **App IDs used**: Appium's build has two IDs, one for the app and one for the test bundle inside. iCtrl WDA uses one for both, so installing it with a free Apple ID takes only one of the App IDs you may create (10 per 7 days).
- **Apple components**: as in Appium's build, Xcode's XCTest and other components are not included; the iPhone's own developer components are used (iOS 17 and later).
- **How it runs**: iCtrl starts it only while Claude reads screen elements and stops it afterwards, because WDA's port (8100) has no authentication and accepts connections from other devices on the network.

If a WDA installed by another tool is already on the iPhone, iCtrl can use it. With both, iCtrl uses iCtrl WDA.

### How Claude reads the screen

How Claude reads the iPhone's screen depends on the helper app and on what is on the screen.

- **With the helper app**: Claude reads the buttons and text on the screen as a list and presses them by name. No images are sent, so it is fast and uses fewer of Claude's tokens.
- **Without the helper app**: the text is read from a picture of the screen with Windows' text recognition. Icons without text cannot be read that way, so Claude looks at the screen image to find where to press.

Even with the helper app, Claude looks at the screen image when:

- What it wants to press is not in the list, such as an icon without text, or an app that draws its screen as a picture (games, maps, drawing apps).
- It must judge what an image shows, such as a photo or a map.
- The helper app could not start (its signature expired, it is not trusted, and so on). Reading then switches to text recognition.

## A shortcut for faster copying

To bring text copied on the iPhone to the PC quickly, iCtrl uses an iPhone shortcut named "ictrlcopy". iCtrl works without it, but with some apps such as Gmail, copied text then takes about a minute to reach the PC (about a second with ictrlcopy).

With the helper app (iCtrl WDA) installed, Claude can make the shortcut for you, and you can watch it work the Shortcuts app on the iCtrl screen.

```
Use iCtrl to operate the iPhone and make the shortcut "ictrlcopy". The steps are in "クリップボード連携のしくみ" of https://github.com/hinatamaxxx/ictrl/blob/main/docs/technical.md.
```

To make it yourself, in the iPhone's Shortcuts app:

1. Tap "New Shortcut", then "Edit" at the top right.
2. Using the search field at the bottom, add "Get Clipboard", "Get Text from Input" and "If", in this order.
3. Set the If condition to "has any value", and put "Copy to Clipboard" (copying "Text") inside the If branch.
4. Search for "Keyboard" and add it (the shortcut then runs when a keyboard connects).
5. Name it "ictrlcopy".

We recommend turning off "Allow Running When Locked" in the settings of ictrlcopy, so it does not run while the iPhone is locked.

## Staying safe

- iCtrl only operates an iPhone connected over USB or on the same Wi-Fi network. It cannot be used from outside.
- iCtrl closes the connection to the iPhone when it is not in use: 30 seconds after the screen is closed, when Claude finishes (if the screen is closed), and 5 minutes after Claude's last action. It never reconnects on its own.
- When there has been no connection and no open screen for 1 minute, iCtrl itself exits. It does not start with Windows.
- iCtrl accepts requests only from inside the same PC.

## Troubleshooting

| Problem | What to check |
|---|---|
| USB does not connect | Is the Apple Devices app installed? Reconnect the cable and tap "Trust" on the iPhone |
| Wi-Fi does not connect | Have you connected over USB once? Are the PC and the iPhone on the same Wi-Fi network? |
| "デベロッパモードを有効にしてください" (turn on Developer Mode) | Turn on Settings › Privacy & Security › Developer Mode on the iPhone |
| The screen does not show | During a phone or LINE call, or while recording, the iPhone sends no screen. Press "画面を映す" (Show screen) afterwards |
| Claude cannot use iCtrl | Restart the Claude app. The first start takes about 20 seconds |
| You use AdGuard and the connection drops | AdGuard's localhost filtering can interfere. See [the technical notes](docs/technical.md#windows-での通信の落とし穴adguard-など) (Japanese) |
| Typing English switches to Japanese | This happens with the Meltype input method. Switch the Windows input method to Microsoft IME or another (Windows key + Space) |

The log is at `%LOCALAPPDATA%\iCtrl\logs\ictrl.log`.

## Update and remove

### As a Claude plugin

- **Update:** ask Claude to "update the iCtrl plugin", then restart the Claude app.
- **Remove:** ask Claude to "remove the iCtrl plugin". Settings and pairing records stay on the PC. To remove everything, delete the two folders `%APPDATA%\iCtrl` and `%LOCALAPPDATA%\iCtrl`.

### As an app

- **Update:** extract a new ZIP over the same folder, then do step 3 of the setup again.
- **Remove:** uninstall iCtrl in Windows Settings › Apps › Installed apps. It first asks whether to keep settings and data. The extracted folder stays; delete it if you no longer need it.

## More details

The architecture, the REST API, the AI tools, how the helper app runs, automatic passcode entry and more are in [the technical notes](docs/technical.md) (Japanese).

## License

iCtrl is released under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later). The licenses of the libraries it uses are listed in [the technical notes](docs/technical.md#ライセンス). iPhone, iOS and Xcode are trademarks of Apple Inc. iCtrl is not affiliated with, endorsed or sponsored by Apple Inc.
