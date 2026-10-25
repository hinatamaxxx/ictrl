# utsushite — control an iPhone from Windows

[日本語](README.md) | English

utsushite lets the AI on your PC (such as Claude) operate your iPhone. Ask Claude something like "open Settings on the iPhone" and Claude does it. The iPhone's screen is shown on the PC, so you can watch and also use the iPhone yourself with the mouse and keyboard, over a USB cable or the same Wi-Fi network.

| Claude using the iPhone's Calculator | The iPhone's screen on the PC |
|---|---|
| ![The utsushite screen while Claude calculates 12×34 in Calculator; "AI at work" at the top and purple dots where Claude pressed](docs/screenshots/en/ai-calculator.jpg) | ![The iPhone's Home Screen shown in utsushite](docs/screenshots/en/home.jpg) |

"AI at work" shows at the top, and a purple dot appears where Claude presses.

## What it does

- Shows the iPhone's screen on the PC (no sound)
- Click to tap, scroll with the mouse wheel, type with the PC keyboard (Japanese input included)
- Copy and paste text and images between the iPhone and the PC
- Saves screenshots of the iPhone on the PC
- Lets Claude operate the iPhone for you

## What you need

| Item | Notes |
|---|---|
| A Windows 10 or 11 PC | Macs are not supported |
| The Claude desktop app (or Codex) | utsushite is installed as a plugin of Claude Code (the app's "Code" tab) or of Codex |
| The Apple Devices app | Needed to connect the iPhone over USB. Free from the [Microsoft Store](https://apps.microsoft.com/detail/9np83lwlpz9k) (not needed if iTunes is installed) |
| An iPhone | iOS 17.4 or later, with **Developer Mode** on (Settings › Privacy & Security › Developer Mode; the iPhone restarts when you turn it on) |
| A USB cable | Only for the first connection. After that, Wi-Fi works too |

You do not need to install Python or anything else yourself: the first time utsushite starts, it downloads what it needs (about 200 MB, some 20 seconds).

## Install

### As a Claude plugin

1. Open the Claude desktop app and start a new session in the "Code" tab.
2. Ask:

   ```
   Install the utsushite plugin: marketplace hinatamaxxx/utsushite, plugin utsushite@utsushite.
   ```

   Claude runs these two commands. You can also type them in a terminal yourself.

   ```bash
   claude plugin marketplace add hinatamaxxx/utsushite
   ```

   ```bash
   claude plugin install utsushite@utsushite
   ```

3. Restart the Claude app.
4. In a new session, ask "Open the utsushite screen". When the utsushite screen appears in the browser on the right side of the app, you are done.

### As a Codex plugin

It works the same way in Codex. Ask Codex to "install the utsushite plugin: marketplace hinatamaxxx/utsushite, plugin utsushite@utsushite", or run these two commands in a terminal.

```bash
codex plugin marketplace add hinatamaxxx/utsushite
```

```bash
codex plugin add utsushite@utsushite
```

Then ask "open the utsushite screen" in a new thread. Below, read "Claude" as Codex.

### Use it as an app, without Claude

utsushite also runs as an ordinary Windows app without Claude (opened from the Start menu, in its own window; it needs Python). To use it that way, give an AI agent such as Claude Code or Codex this repository's URL and ask it to "install it as an app": it follows [AGENTS.md](AGENTS.md).

## Connect the iPhone for the first time

1. Connect the iPhone to the PC with a USB cable.
2. On the utsushite screen, press "Connect via USB".
3. When the iPhone asks "Trust This Computer?", tap "Trust" and enter the iPhone's passcode.
4. When the iPhone's screen appears, you are done.

This also prepares the Wi-Fi connection. From then on, if the PC and the iPhone are on the same Wi-Fi network, press "Connect via Wi-Fi", with no cable.

## Start and stop

utsushite is made of a part that runs in the background (the server) and the screen that shows and controls the iPhone. You never need to start or stop the server yourself.

- **Start**: ask Claude to "open the utsushite screen", or to do something on the iPhone: the server starts on its own. The screen appears in the browser on the right side of the Claude app.
- **Stop**: 30 seconds after the screen is closed, the iPhone connection closes, and the server exits 1 minute later. The short wait keeps a screen that was only hidden for a moment from losing its connection. While Claude uses utsushite, it keeps running.

While only the server runs, no icon appears on the taskbar or in the notification area. Installed as an app, utsushite starts from "utsushite" in the Start menu and ends as soon as you close its window.

### Start the server by hand (for advanced users)

You do not normally need this, but the server can be started with a command. Run this in PowerShell, replacing `<version>` with the newest version folder's name in `%USERPROFILE%\.claude\plugins\cache\utsushite\utsushite\`.

```powershell
& "$env:USERPROFILE\.claude\plugins\cache\utsushite\utsushite\<version>\plugin\utsushite.cmd" url
```

The server starts in the background and prints the screen's URL (`http://127.0.0.1:47800/`). The commands for an app installation are in [the technical notes, "セットアップ"](docs/technical.md#セットアップclaude-のプラグインを使わない場合) (Japanese).

The screen also works in an ordinary browser at `http://127.0.0.1:47800/`. A server started by hand also exits by itself after 1 minute unused. There is no command to stop it: wait for it to exit, or end `pythonw.exe` in Task Manager.

## Everyday use

1. Ask Claude to "open the utsushite screen".
2. Choose "USB" or "Wi-Fi" at the top of the screen to connect.
3. When you are done, press "Disconnect" or close the screen.

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
| Screenshot | Ctrl+S, or the camera button in the bottom bar. Saved in Pictures › utsushite |
| Show or hide the screen | The iPhone-shaped button in the bottom bar |

Unlock the iPhone yourself when it is locked.

The screen is shown in English or Japanese, following the language of your PC (browser). To change it, choose one under "言語 / Language" in the menu.

## Ask Claude to operate the iPhone

With utsushite installed, ask Claude, for example:

```
On the iPhone, open the Settings app and show the Wi-Fi screen.
```

- When Claude starts using utsushite, it opens the utsushite screen in the browser on the right side of the app, so you can watch the iPhone and what Claude does.
- While Claude works, the utsushite screen shows "AI at work" at the top, and a purple dot where Claude touches.
- Press the ✕ on "AI at work" to stop it at once. Use the mouse to take over at any time.
- Claude never enters the iPhone's passcode.

### Install the helper app (WebDriverAgent)

Claude operates the iPhone by reading the buttons and text on its screen. With the helper app "utsushite WDA" (WebDriverAgent) on the iPhone, Claude reads the kind and position of each button and text as a list, and presses them by name without looking at images. utsushite also works without it (see [How Claude reads the screen](#how-claude-reads-the-screen)).

The helper app is distributed unsigned. Sign it with your own Apple ID when you install it: Apple requires this, and utsushite cannot skip it.

1. **Download.** Get `utsushite-WDA.ipa` from [the "utsushite WDA" release](https://github.com/hinatamaxxx/utsushite/releases/tag/wda).
2. **Sign and install.** Install [AltStore Classic](https://altstore.io/) (used together with AltServer on the PC) or [Sideloadly](https://sideloadly.io/) on the PC, connect the iPhone over USB, and install `utsushite-WDA.ipa` with your own Apple ID (a free Apple ID is fine).
3. **Trust it on the iPhone.** In Settings › General › VPN & Device Management, trust your Apple ID.
4. When you ask Claude to do something, utsushite uses the helper app on its own. You do not need to open it from the Home Screen.

With a free Apple ID, the helper app stops working after 7 days. Install it again with step 2 (AltStore Classic only needs a refresh).

The first time the helper app starts, the iPhone may ask for its passcode. Enter it on the iPhone yourself, or set it up in utsushite's menu, "Passcode entry for WDA".

`utsushite-WDA.ipa` is Appium's published WebDriverAgent build with only the app ID and the display name changed. The WebDriverAgent license (BSD-3-Clause) is `WebDriverAgent-LICENSE.txt` in the same release. Instead of downloading it, you can also ask Claude to "make the utsushite helper app IPA": it builds the same file on the PC, on the desktop.

### How utsushite WDA differs from other WebDriverAgent builds

WebDriverAgent (WDA) is an app widely used by iPhone test automation tools such as Appium. utsushite WDA is Appium's official build prepared for utsushite, and it works the same. The differences:

- **Name and ID**: its Home Screen name is "utsushite WDA" and its app ID is `com.utsushite.wda`. The program inside is not rebuilt; it is Appium's as published.
- **App IDs used**: Appium's build has two IDs, one for the app and one for the test bundle inside. utsushite WDA uses one for both, so installing it with a free Apple ID takes only one of the App IDs you may create (10 per 7 days).
- **Apple components**: as in Appium's build, Xcode's XCTest and other components are not included; the iPhone's own developer components are used (iOS 17 and later).
- **How it runs**: utsushite starts it only while Claude reads screen elements and stops it afterwards, because WDA's port (8100) has no authentication and accepts connections from other devices on the network.

If a WDA installed by another tool is already on the iPhone, utsushite can use it. With both, utsushite uses utsushite WDA.

### How Claude reads the screen

How Claude reads the iPhone's screen depends on the helper app and on what is on the screen.

- **With the helper app**: Claude reads the buttons and text on the screen as a list and presses them by name. No images are sent, so it is fast and uses fewer of Claude's tokens.
- **Without the helper app**: the text is read from a picture of the screen with Windows' text recognition. Icons without text cannot be read that way, so Claude looks at the screen image to find where to press.

Even with the helper app, Claude looks at the screen image when:

- What it wants to press is not in the list, such as an icon without text, or an app that draws its screen as a picture (games, maps, drawing apps).
- It must judge what an image shows, such as a photo or a map.
- The helper app could not start (its signature expired, it is not trusted, and so on). Reading then switches to text recognition.

## A shortcut for faster copying

To bring text copied on the iPhone to the PC quickly, utsushite uses an iPhone shortcut named "utsushitecopy". utsushite works without it, but with some apps such as Gmail, copied text then takes about a minute to reach the PC (about a second with utsushitecopy).

Claude can make the shortcut for you, and you can watch it work the Shortcuts app on the utsushite screen. It also works without the helper app (utsushite WDA) (verified), but then Claude works from images of the screen, which takes longer, and a missed tap sometimes has to be redone.

```
Use utsushite to operate the iPhone and make the shortcut "utsushitecopy". The steps are in "クリップボード連携のしくみ" of https://github.com/hinatamaxxx/utsushite/blob/main/docs/technical.md.
```

To make it yourself, in the iPhone's Shortcuts app:

1. Tap "New Shortcut", then "Edit" at the top right.
2. Using the search field at the bottom, add "Get Clipboard", "Get Text from Input" and "If", in this order.
3. Set the If condition to "has any value", and put "Copy to Clipboard" (copying "Text") inside the If branch.
4. Search for "Keyboard" and add it (the shortcut then runs when a keyboard connects).
5. Name it "utsushitecopy".

We recommend turning off "Allow Running When Locked" in the settings of utsushitecopy, so it does not run while the iPhone is locked.

## Staying safe

- utsushite only operates an iPhone connected over USB or on the same Wi-Fi network. It cannot be used from outside.
- utsushite closes the connection to the iPhone when it is not in use, and never reconnects on its own. It closes it:
  - when Claude or Codex finishes, even if the utsushite screen is open in its browser (not while you have the app window open);
  - after 5 minutes with no operation by a person or by the AI, even with the utsushite screen open;
  - 30 seconds after the utsushite screen is closed or hidden.
- To keep the connection, turn on "Keep the connection even when unused (not recommended)" in the menu.
- When there has been no connection and no open screen for 1 minute, utsushite itself exits. It does not start with Windows.
- utsushite accepts requests only from inside the same PC.

## Troubleshooting

| Problem | What to check |
|---|---|
| USB does not connect | Is the Apple Devices app installed? Reconnect the cable and tap "Trust" on the iPhone |
| Wi-Fi does not connect | Have you connected over USB once? Are the PC and the iPhone on the same Wi-Fi network? |
| "Turn on Developer Mode on the iPhone" | Turn on Settings › Privacy & Security › Developer Mode on the iPhone |
| The screen does not show | During a phone or LINE call, or while recording, the iPhone sends no screen. Press "Show the screen" afterwards |
| Claude cannot use utsushite | Restart the Claude app. The first start takes about 20 seconds |
| You use AdGuard and the connection drops | AdGuard's localhost filtering can interfere. See [the technical notes](docs/technical.md#windows-での通信の落とし穴adguard-など) (Japanese) |
| Typing English switches to Japanese | This happens with the Meltype input method. Switch the Windows input method to Microsoft IME or another (Windows key + Space) |

The log is at `%LOCALAPPDATA%\utsushite\logs\utsushite.log`.

## Update and remove

- **Update:** ask Claude to "update the utsushite plugin", then restart the Claude app.
- **Remove:** ask Claude to "remove the utsushite plugin". Settings and pairing records stay on the PC. To remove everything, delete the two folders `%APPDATA%\utsushite` and `%LOCALAPPDATA%\utsushite`.

For the Codex plugin, run these in a terminal.

- **Update:** run `codex plugin marketplace upgrade utsushite`, then `codex plugin add utsushite@utsushite` again, and start a new thread.
- **Remove:** run `codex plugin remove utsushite@utsushite`.

For an app installation, ask an AI agent to update or remove it.

## More details

The architecture, the REST API, the AI tools, how the helper app runs, automatic passcode entry and more are in [the technical notes](docs/technical.md) (Japanese).

## License

utsushite is released under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later). The licenses of the libraries it uses are listed in [the technical notes](docs/technical.md#ライセンス). iPhone, iOS and Xcode are trademarks of Apple Inc. utsushite is not affiliated with, endorsed or sponsored by Apple Inc.
