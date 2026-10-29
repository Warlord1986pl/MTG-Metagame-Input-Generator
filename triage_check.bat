@echo off
rem Piatkowy alarm triage-u (recznie: podwojne klikniecie). Harmonogram zadan uruchamia python bezposrednio
rem (docs\triage_check_task.xml), bez pause. Kod: 0 complete, 1 pending_notice, 2 pending_blocking, 3 blad.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "VENV=E:\github\.venv"
if not exist "%VENV%\Scripts\activate.bat" (
  echo STOP: brak srodowiska %VENV%
  pause
  exit /b 3
)
call "%VENV%\Scripts\activate.bat"
python triage_check.py %*
set "RC=%ERRORLEVEL%"
echo.
echo Kod wyjscia: %RC%
pause
exit /b %RC%
