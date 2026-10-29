@echo off
rem Runner tygodnia: podwojne klikniecie = etap DATA dla ostatniego zakonczonego tygodnia ISO.
rem Argumenty (np. --week 2026-W44 --stage all --force) sa przekazywane dalej. Opis: docs\runner.md
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "VENV=E:\github\.venv"
if not exist "%VENV%\Scripts\activate.bat" (
  echo STOP: brak srodowiska %VENV% ^(docs\runner_config.csv, pipeline_python^)
  pause
  exit /b 3
)
call "%VENV%\Scripts\activate.bat"
python metagame_week.py %*
set "RC=%ERRORLEVEL%"
echo.
echo Kod wyjscia: %RC%
pause
exit /b %RC%
