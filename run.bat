::[Bat To Exe Converter]
::
::YAwzoRdxOk+EWAnk
::fBw5plQjdG8=
::YAwzuBVtJxjWCl3EqQJgSA==
::ZR4luwNxJguZRRnk
::Yhs/ulQjdF+5
::cxAkpRVqdFKZSDk=
::cBs/ulQjdF+5
::ZR41oxFsdFKZSDk=
::eBoioBt6dFKZSDk=
::cRo6pxp7LAbNWATEpCI=
::egkzugNsPRvcWATEpCI=
::dAsiuh18IRvcCxnZtBJQ
::cRYluBh/LU+EWAnk
::YxY4rhs+aU+JeA==
::cxY6rQJ7JhzQF1fEqQJQ
::ZQ05rAF9IBncCkqN+0xwdVs0
::ZQ05rAF9IAHYFVzEqQJQ
::eg0/rx1wNQPfEVWB+kM9LVsJDGQ=
::fBEirQZwNQPfEVWB+kM9LVsJDGQ=
::cRolqwZ3JBvQF1fEqQJQ
::dhA7uBVwLU+EWDk=
::YQ03rBFzNR3SWATElA==
::dhAmsQZ3MwfNWATElA==
::ZQ0/vhVqMQ3MEVWAtB9wSA==
::Zg8zqx1/OA3MEVWAtB9wSA==
::dhA7pRFwIByZRRnk
::Zh4grVQjdCyDJGyX8VAjFBlbQw++GG6pDaET+NTX9u6Oo3ERTeY2ebPXw7CHIa4W8kCE
::YB416Ek+ZG8=
::
::
::978f952a14a936cc963da21a135fa983
@echo off
REM ============================================================
REM  Lydia launcher
REM  - starts the backend (voice + brain + server)
REM  - opens the UI in its own app window
REM  - CLOSE THE LYDIA WINDOW -> the whole system shuts down
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".env" (
  echo [Lydia] No .env found. Copy .env.example to .env and add your keys first.
  pause
  exit /b 1
)

REM --- find a Chromium browser to host the app window ---
set "BROWSER="
for %%P in (
  "%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
  "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
) do if exist "%%~P" if not defined BROWSER set "BROWSER=%%~P"

REM --- start the backend (this .bat owns the UI window, so disable the app's own) ---
set LYDIA_OPEN_UI=0
echo [Lydia] starting backend...
start "LYDIA_TAI_BACKEND" /min cmd /c "python -m brain.main"

REM --- wait until the server is accepting connections ---
set /a tries=0
:waitloop
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',8766);$c.Close()}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
  set /a tries+=1
  if !tries! GEQ 40 ( echo [Lydia] backend didn't start - check the LYDIA_TAI_BACKEND window. & goto shutdown )
  timeout /t 1 /nobreak >nul
  goto waitloop
)
echo [Lydia] ready at http://localhost:8766

REM --- open the UI; block until it is closed ---
if defined BROWSER (
  echo [Lydia] Close the Lydia window to shut everything down.
  start /wait "" "!BROWSER!" --app=http://localhost:8766 --user-data-dir="%LOCALAPPDATA%\LydiaTAIUI" --no-first-run --new-window
) else (
  start "" http://localhost:8766
  echo [Lydia] Running. Close THIS window to shut Lydia down.
  pause
)

:shutdown
echo [Lydia] shutting down...
REM kill whatever owns port 8766 (the backend) + its process tree
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" >nul 2>&1
taskkill /F /T /FI "WINDOWTITLE eq LYDIA_TAI_BACKEND*" >nul 2>&1
echo [Lydia] stopped.
endlocal
exit /b 0
