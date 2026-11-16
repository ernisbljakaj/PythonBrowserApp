@echo off
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Windowssystemhelper32 --icon windows.ico --add-data "windows.ico;." AIBrowser.py
