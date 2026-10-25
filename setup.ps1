<#
.SYNOPSIS
  utsushite のセットアップ。仮想環境を作り依存関係を入れる。オプションでショートカットや AI 連携を登録する。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File setup.ps1
  powershell -ExecutionPolicy Bypass -File setup.ps1 -Shortcut -Claude -Codex
  （アンインストール: 「設定 → アプリ → インストールされているアプリ」の utsushite、または uninstall.ps1）
#>
param(
  [switch]$Shortcut,   # スタートメニューに「utsushite」を作る
  [switch]$Desktop,    # デスクトップにも「utsushite」を作る
  [switch]$Claude,     # Claude Code（ユーザー全体）に MCP サーバーと応答終了時の停止フックを登録する
  [switch]$Codex       # Codex の ~/.codex/config.toml に MCP サーバーを追記する
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'

# Python 3.13 以上が必要: Wi-Fi トンネルの TLS-PSK を標準の ssl で行うため
# （3.12 では sslpsk_pmd3 が OpenSSL 1.1 の DLL を要求して読み込めなかった）。
# Python は利用者が python.org 版を入れる（utsushite は同梱も自動取得もしない）。
# Microsoft Store 版は使わない: パッケージ化されたアプリなので、AppData への書き込みが
# Store の Python 専用の場所へ振り替えられ、設定やペアリング記録が別の場所に分かれる。
function Find-Python {
  # py ランチャーの既定（入っている中で最新の版）を優先する。
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $p = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p.Trim() }
  }
  foreach ($name in @('python', 'python3')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
  }
  foreach ($exe in $candidates) {
    if ($exe -like '*\WindowsApps\*') { continue }
    & $exe -c "import sys; sys.exit(sys.version_info < (3, 13))" 2>$null
    if ($LASTEXITCODE -eq 0) { return $exe }
  }
  return $null
}

if (-not (Test-Path $python)) {
  $base = Find-Python
  if (-not $base) {
    throw ("Python 3.13 以上（python.org 版）が見つかりません。次のどちらかで入れてから、もう一度実行してください。`n" +
           "  winget install Python.Python.3.14`n" +
           "  https://www.python.org/downloads/ のインストーラー（「Add python.exe to PATH」にチェック）`n" +
           "Microsoft Store 版の Python は使えません（AppData の書き込みが振り替えられるため）。")
  }
  Write-Host "仮想環境を作っています（$base）…"
  & $base -m venv $venv
}
& $python -c "import sys; sys.exit(sys.version_info < (3, 13))"
if ($LASTEXITCODE) { throw '.venv が Python 3.13 未満です。.venv を削除してから再実行してください' }

Write-Host '依存関係をインストールしています…'
& $python -m pip install -q --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE) { throw 'pip の更新に失敗しました' }
# requirements.txt は版を固定した一覧。pymobiledevice3 を自身の依存一覧なしで入れるため
# --no-deps で入れる（その一覧には utsushite が使わない IPython・PyAV などが含まれる）。
# PyPI にソースしかない lzfse・pylzss・hexdump は、配布元がビルドした wheels\ の版を使う
# （tools\build_wheels.py）。--only-binary で、利用者の PC では何もビルドしない。
& $python -m pip install -q --disable-pip-version-check --no-deps --only-binary ':all:' `
  --find-links (Join-Path $root 'wheels') -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE) { throw '依存関係のインストールに失敗しました' }
& $python -m pip install -q --disable-pip-version-check --no-deps -e $root
if ($LASTEXITCODE) { throw 'utsushite のインストールに失敗しました' }
# USB の iPhone とは Apple Devices アプリ（または iTunes）のドライバとサービスで通信する。
# 入れるかどうかは利用者が決める（Microsoft Store のアプリなので、ここでは入れない）。
# Store 版はパッケージ内の AppleMobileDeviceProcess.exe が仲介し、Windows サービスには現れない。
$appleDevices = Get-AppxPackage -Name 'AppleInc.AppleDevices' -ErrorAction SilentlyContinue
$itunesService = Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue
if (-not $appleDevices -and -not $itunesService) {
  Write-Warning ("Apple Devices アプリが見つかりません。USB で iPhone につなぐには必要です。次のどちらかで入れてください。`n" +
                 "  winget install --id 9NP83LWLPZ9K --source msstore`n" +
                 "  https://apps.microsoft.com/detail/9np83lwlpz9k")
}
Write-Host 'OK: utsushite をインストールしました'

# utsushite のアイコン（%LOCALAPPDATA%\utsushite\utsushite.ico。ui/icon.svg と同じ絵）
$icon = (& $python -c "from utsushite.icon import ensure_ico; print(ensure_ico())").Trim()
# utsushite のウィンドウ（WebView2）を Windows 標準の C# コンパイラーでビルドしておく
& $python -c "from utsushite import window; window.build()"
if ($LASTEXITCODE -ne 0) { Write-Warning 'utsushite のウィンドウをビルドできませんでした（最初に開くときにもう一度試します）' }

function New-UtsushiteShortcut([string]$dir) {
  $shell = New-Object -ComObject WScript.Shell
  $lnk = $shell.CreateShortcut((Join-Path $dir 'utsushite.lnk'))
  $lnk.TargetPath = $pythonw
  $lnk.Arguments = '-m utsushite open'
  $lnk.WorkingDirectory = $root
  $lnk.IconLocation = "$icon,0"
  $lnk.Description = 'Windows から iPhone を操作'
  $lnk.Save()
}

if ($Shortcut) {
  # スタートメニューはアプリ 1 つにつき 1 つ（フォルダーを作らない）
  New-UtsushiteShortcut (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs')
  Write-Host 'OK: スタートメニューに utsushite を作成しました'
}
if ($Desktop) {
  New-UtsushiteShortcut ([Environment]::GetFolderPath('Desktop'))
  Write-Host 'OK: デスクトップに utsushite を作成しました'
}

# 「設定 → アプリ → インストールされているアプリ」に載せ、そこからアンインストールできるようにする。
# utsushite が書くレジストリはこのアンインストール情報 1 か所だけ（ユーザーごと、HKCU）。
$version = (& $python -c "import utsushite; print(utsushite.__version__)").Trim()
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\utsushite'
$uninstallPs1 = Join-Path $root 'uninstall.ps1'
New-Item -Path $uninstallKey -Force | Out-Null
$sizeKB = [int]((Get-ChildItem $root -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1KB)
$values = @{
  DisplayName = 'utsushite'; DisplayVersion = $version; Publisher = 'hinatamaxxx'
  DisplayIcon = $icon; InstallLocation = $root
  UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstallPs1`""
  QuietUninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstallPs1`" -Quiet"
}
foreach ($k in $values.Keys) { Set-ItemProperty -Path $uninstallKey -Name $k -Value $values[$k] }
Set-ItemProperty -Path $uninstallKey -Name EstimatedSize -Value $sizeKB -Type DWord
Set-ItemProperty -Path $uninstallKey -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $uninstallKey -Name NoRepair -Value 1 -Type DWord
Write-Host 'OK: 「インストールされているアプリ」に utsushite を登録しました'

if ($Claude) {
  & claude mcp add --scope user utsushite -- $python -m utsushite mcp
  Write-Host 'OK: Claude Code に utsushite を登録しました'
  # Claude の応答が終わったら WebDriverAgent と「AI が操作中」を止める
  & $python -m utsushite claude-hook
}

if ($Codex) {
  $config = Join-Path $HOME '.codex\config.toml'
  $text = if (Test-Path $config) { Get-Content $config -Raw -Encoding utf8 } else { '' }
  if ($text -match '(?m)^\[mcp_servers\.utsushite\]') {
    Write-Host 'Codex: 既に登録済みです'
  } else {
    $entry = "`n[mcp_servers.utsushite]`ncommand = '$python'`nargs = [`"-m`", `"utsushite`", `"mcp`"]`n"
    Add-Content -Path $config -Value $entry -Encoding utf8
    Write-Host 'OK: Codex に utsushite を登録しました'
  }
}
