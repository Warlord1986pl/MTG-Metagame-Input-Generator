@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo === MTG Metagame Generator ===
echo.
echo Uruchomiam generator...
echo.
python src/metagame_input_generator.py
echo.
pause
