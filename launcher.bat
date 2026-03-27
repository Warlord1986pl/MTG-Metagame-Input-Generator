@echo off
chcp 65001 > nul
cd /d "%~dp0"

:menu
cls
echo.
echo ╔════════════════════════════════════════╗
echo ║   MTG Metagame Input Generator v1.0    ║
echo ╚════════════════════════════════════════╝
echo.
echo 1. Pobierz dane z API (generator)
echo 2. Otwórz edytor tabel
echo 3. O programie
echo 0. Wyjdź
echo.
set /p choice="Wybierz opcję (0-3): "

if "%choice%"=="1" goto generator
if "%choice%"=="2" goto editor
if "%choice%"=="3" goto about
if "%choice%"=="0" goto exit
cls
echo Błędny wybór. Spróbuj ponownie.
timeout /t 2 > nul
goto menu

:generator
cls
echo.
echo === Generator Metametagame ===
echo.
start cmd /k python src/metagame_input_generator.py
goto menu

:editor
cls
echo.
echo === Edytor Tabel ===
echo.
start cmd /k python src/preset_cli.py
goto menu

:about
cls
echo.
echo MTG Metagame Input Generator
echo Wersja: 1.0
echo.
echo Program do pobierania danych metagry MTG z API
echo i edycji wyników w interaktywnych tabelach.
echo.
pause
goto menu

:exit
exit /b 0
