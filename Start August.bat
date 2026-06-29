@echo off
title August — Voice AI
color 0A

:: ─────────────────────────────────────────────────────────────────────────────
:: Start August.bat
:: Launches the August Voice AI assistant and handles startup registration.
:: ─────────────────────────────────────────────────────────────────────────────

:: Get the directory this bat file lives in (the project root)
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

:: Path to Python and the main script
set "PYTHON=python"
set "SCRIPT=%PROJECT_DIR%\august.py"
set "STARTUP_CONFIG=%PROJECT_DIR%\StartOnBoot.txt"

:: ── Read StartOnBoot setting ──────────────────────────────────────────────────
set "START_ON_BOOT=false"
for /f "tokens=2 delims=: " %%A in ('findstr /i "StartOnBoot" "%STARTUP_CONFIG%"') do (
    set "START_ON_BOOT=%%A"
)

:: Normalise to lowercase comparison
set "START_ON_BOOT_LOWER=%START_ON_BOOT%"
if /i "%START_ON_BOOT%"=="True"  set "START_ON_BOOT_LOWER=true"
if /i "%START_ON_BOOT%"=="False" set "START_ON_BOOT_LOWER=false"

:: ── Startup shortcut path ─────────────────────────────────────────────────────
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_FOLDER%\August Voice AI.lnk"

:: ── Apply startup preference ──────────────────────────────────────────────────
if "%START_ON_BOOT_LOWER%"=="true" (
    echo  [*] StartOnBoot is ON -- registering August in Windows Startup...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\register_startup.ps1"
) else (
    echo  [*] StartOnBoot is OFF -- removing August from Windows Startup...
    if exist "%SHORTCUT%" (
        del "%SHORTCUT%"
        echo  [OK] Startup shortcut removed.
    ) else (
        echo  [--] No startup shortcut found. Nothing to remove.
    )
)

echo.
echo ╔══════════════════════════════════════════════╗
echo ║          A U G U S T  —  Voice AI            ║
echo ╚══════════════════════════════════════════════╝
echo.
echo  Starting August... say "Hey August" to activate!
echo  Close this window or press Ctrl+C to stop August.
echo.

:: ── Launch August ─────────────────────────────────────────────────────────────
cd /d "%PROJECT_DIR%"
%PYTHON% -u "%SCRIPT%"

:: If Python exits cleanly, keep the window open briefly so errors can be read
echo.
echo  August has stopped. Press any key to close...
pause > nul
