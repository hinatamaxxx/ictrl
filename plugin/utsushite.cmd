@echo off
rem utsushite's entry point for the Claude Code plugin and other MCP clients:
rem   utsushite.cmd mcp         the MCP server (stdio passes straight through cmd)
rem   utsushite.cmd stop-hook   the Claude Code Stop hook
rem
rem uv on PATH is used as it is. Without it, the pinned uv release is fetched
rem once from GitHub, checked against its SHA256 and kept in this folder's .uv\
rem with the Python and cache it makes, so removing the plugin removes them.
rem Messages are ASCII: cmd reads this file in the console's code page.
rem Progress goes to stderr; stdout is the MCP connection.
setlocal
for %%i in ("%~dp0..") do set "ROOT=%%~fi"

if /i "%~1"=="stop-hook" (
  rem never sets anything up: without the environment nothing was started
  if not exist "%ROOT%\.venv\Scripts\python.exe" exit /b 0
  "%ROOT%\.venv\Scripts\python.exe" -m utsushite stop-hook
  exit /b 0
)

set "UV="
for %%u in (uv.exe) do set "UV=%%~$PATH:u"
if not defined UV (
  set "UV=%ROOT%\.uv\bin\uv.exe"
  set "UV_CACHE_DIR=%ROOT%\.uv\cache"
  set "UV_PYTHON_INSTALL_DIR=%ROOT%\.uv\python"
  if not exist "%ROOT%\.uv\bin\uv.exe" call :fetch || exit /b 1
)
"%UV%" run --quiet --no-project --managed-python --python 3.14 "%ROOT%\plugin\launch.py" %*
exit /b %errorlevel%

:fetch
set "UV_VERSION=0.13.5"
set "UV_SHA256=a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a"
set "UV_URL=https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
echo utsushite: uv was not found; fetching uv %UV_VERSION% from GitHub (about 15 MB) 1>&2
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue';" ^
  "[Net.ServicePointManager]::SecurityProtocol = 'Tls12';" ^
  "$dir = Join-Path $env:ROOT '.uv'; $zip = Join-Path $dir 'uv.zip';" ^
  "New-Item -ItemType Directory -Force $dir | Out-Null;" ^
  "Invoke-WebRequest -UseBasicParsing $env:UV_URL -OutFile $zip;" ^
  "if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $env:UV_SHA256) { Remove-Item $zip; throw 'the SHA256 of the downloaded uv does not match' }" ^
  "Expand-Archive $zip (Join-Path $dir 'bin') -Force; Remove-Item $zip" 1>&2
if errorlevel 1 (
  echo utsushite: could not fetch uv. Install it with "winget install astral-sh.uv" and try again. 1>&2
  exit /b 1
)
exit /b 0
