@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo === MTG Metagame Studio ===
echo.
echo Uruchamiam interfejs PySide6...
echo.
set "VENV_PYTHONW=%~dp0..\.venv\Scripts\pythonw.exe"
set "VENV_PYTHON=%~dp0..\.venv\Scripts\python.exe"

if exist "%VENV_PYTHONW%" (
	start "" "%VENV_PYTHONW%" "%~dp0src\gui_app.py"
	goto :eof
)

if exist "%VENV_PYTHON%" (
	start "" "%VENV_PYTHON%" "%~dp0src\gui_app.py"
	goto :eof
)

python "%~dp0src\gui_app.py"
echo.
pause