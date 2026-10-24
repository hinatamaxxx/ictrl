@echo off
rem Open iCtrl (starts the background server if needed)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m ictrl open
