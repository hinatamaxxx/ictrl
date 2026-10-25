@echo off
rem Open utsushite (starts the background server if needed)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m utsushite open
