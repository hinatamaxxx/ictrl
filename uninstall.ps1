<#
.SYNOPSIS
  utsushite のアンインストール。「設定 → アプリ → インストールされているアプリ」からも呼ばれる。

  既定では、設定とデータ（%APPDATA%\utsushite、%LOCALAPPDATA%\utsushite のログ・ペアリング記録など、
  Windows 資格情報マネージャーの保存パスコード）もすべて削除する。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File uninstall.ps1                 # 確認してから削除
  powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Quiet          # 確認なし・すべて削除
  powershell -ExecutionPolicy Bypass -File uninstall.ps1 -KeepSettings   # 設定とデータを残す
#>
param(
  [switch]$Quiet,
  [switch]$KeepSettings
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not $Quiet) {
  Write-Host 'utsushite をアンインストールします。'
  Write-Host '  Enter : 設定とデータもすべて削除する（既定）'
  Write-Host '  K     : 設定とデータを残す（再インストールしたときに引き継ぐ）'
  Write-Host '  Q     : やめる'
  $answer = (Read-Host '選んでください').Trim().ToUpper()
  if ($answer -eq 'Q') { Write-Host 'やめました'; exit 0 }
  if ($answer -eq 'K') { $KeepSettings = $true }
}

# 1. 動いている utsushite（ウィンドウ・サーバー・MCP）を止める（ウィンドウの WebView2 も一緒に終わる）
Get-Process UtsushiteWindow -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match ' -m utsushite ' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 2. ショートカット（スタートメニュー・デスクトップ・スタートアップ）
foreach ($dir in @(
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'),
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('Startup'))) {
  Remove-Item (Join-Path $dir 'utsushite.lnk') -ErrorAction SilentlyContinue
}

# 3. AI 連携の登録（Claude Code の MCP とフック、Codex の MCP）
if (Get-Command claude -ErrorAction SilentlyContinue) {
  & claude mcp remove utsushite --scope user 2>$null | Out-Null
}
if (Test-Path $python) { & $python -m utsushite claude-unhook }
$codex = Join-Path $HOME '.codex\config.toml'
if (Test-Path $codex) {
  $text = Get-Content $codex -Raw -Encoding utf8
  $new = [regex]::Replace($text, '(?m)^\[mcp_servers\.utsushite\]\r?\n(?:(?!\[).*\r?\n)*', '')
  if ($new -ne $text) { Set-Content -Path $codex -Value $new -Encoding utf8 -NoNewline }
}

# 4. 設定とデータ
if (-not $KeepSettings) {
  # iCtrl and ~/.ictrl: utsushite's earlier names, in case they were not moved yet
  foreach ($dir in @((Join-Path $env:APPDATA 'utsushite'), (Join-Path $env:LOCALAPPDATA 'utsushite'),
                     (Join-Path $env:APPDATA 'iCtrl'), (Join-Path $env:LOCALAPPDATA 'iCtrl'), (Join-Path $HOME '.ictrl'))) {
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
  }
  foreach ($target in @('utsushite/iPhone passcode', 'utsushite/XCTest automatic entry',
                        'iCtrl/iPhone passcode', 'iCtrl/XCTest automatic entry')) {
    & cmdkey.exe "/delete:$target" 2>$null | Out-Null
  }
}

# 5. 「インストールされているアプリ」の登録（utsushite が書く唯一のレジストリ）
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\utsushite' -Recurse -ErrorAction SilentlyContinue

# 6. プログラム（仮想環境）。ソースのフォルダー自体は残すので、不要なら削除する。
Remove-Item (Join-Path $root '.venv') -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ('OK: utsushite をアンインストールしました' + $(if ($KeepSettings) { '（設定とデータは残しました）' } else { '' }))
Write-Host "ソースのフォルダー（$root）は残っています。不要なら削除してください。"
if (-not $Quiet) { Read-Host '終了するには Enter' | Out-Null }
