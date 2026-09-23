@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Lydia - Setup
color 0A

echo ============================================
echo        Lydia - One-Time Setup
echo ============================================
echo.

REM ============================================================
REM  NOTE: ffmpeg is no longer required. It was only installed so the
REM  old TTS module could decode MP3 to PCM. The voice pipeline was
REM  removed 2026-09-23 (keyboard + UI only), so this step is gone.
REM ============================================================

REM ---- Check Python ----
echo [1/4] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python NOT found. Installing via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if !errorlevel! neq 0 (
        echo   [ERROR] Could not install Python automatically.
        echo   Please install Python 3.10+ from https://www.python.org/downloads/
        echo   and tick "Add Python to PATH" during install.
        pause
        exit /b 1
    )
    echo   Python installed. You may need to reopen this window.
) else (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo   Python !PYVER! found.
)

echo.

REM ---- Check browser ----
echo [2/4] Checking browser...
set BROWSER=
if exist "%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe" set BROWSER=Brave
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set BROWSER=Chrome
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set BROWSER=Chrome
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set BROWSER=Edge
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set BROWSER=Edge
if defined BROWSER (
    echo   %BROWSER% found.
) else (
    echo   [WARN] No Brave, Chrome, or Edge found.
    echo   The launcher will fall back to your default browser.
    echo   Install Brave or Chrome for the full experience.
)

echo.

REM ---- Install Python deps ----
echo [3/4] Installing Python dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo   [ERROR] pip install failed. Check the error above.
    pause
    exit /b 1
)
echo   Dependencies installed.

echo.

REM ---- Check .env ----
echo [4/4] Checking .env file...
if exist ".env" (
    echo   .env found. Good.
) else (
    echo   .env NOT found.
    echo   Copy .env.example to .env and fill in your keys:
    echo     copy .env.example .env
    echo   Then edit .env and add your API keys.
)

echo.
echo ============================================
echo   Setup complete!
echo   - Add your API keys to .env
echo   - Then run run.bat to start Lydia
echo ============================================
echo.
pause
endlocal
