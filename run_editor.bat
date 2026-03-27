@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo === MTG Metagame Editor ===
echo.
echo Uruchomiam edytor tabel...
echo.
python src/preset_cli.py
echo.
pause
