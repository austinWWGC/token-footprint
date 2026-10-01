@echo off
rem Builds dist\TokenFootprint.exe. Needs Python 3.10+ with Tkinter.
cd /d "%~dp0"
if not exist .build-venv python -m venv .build-venv
.build-venv\Scripts\python -m pip install --quiet --upgrade pyinstaller
.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed ^
  --name TokenFootprint --icon icon.ico ^
  --add-data "icon.ico;." --add-data "README.md;." ^
  token_footprint.pyw
