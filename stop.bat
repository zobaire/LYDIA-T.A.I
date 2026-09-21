@echo off
REM ============================================================
REM  Stop Lydia entirely:
REM   1) close her UI windows (Edge/Chrome app windows only)
REM   2) kill the backend (by port + by process)
REM   3) verify port 8766 is clear
REM  Your normal browsing is NOT touched.
REM ============================================================
echo [Lydia] stopping everything...

REM 1) close Lydia's UI app windows (matched by her profile dir / app URL, so
REM    only Lydia's Edge/Chrome windows are closed, not your other tabs)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'msedge.exe' -or $_.Name -eq 'chrome.exe') -and $_.CommandLine -match 'LydiaTAIUI|ui_profile|localhost:8766' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM 2a) kill whatever owns port 8766 (the backend server)
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM 2b) kill the minimized backend console + any lingering lydia.main python
taskkill /F /T /FI "WINDOWTITLE eq LYDIA_TAI_BACKEND*" >nul 2>&1
REM 2b) (blanket '*brain.main*' kill removed - it would have killed BOTH Lydia instances; kill is scoped by port + window title above)

REM 3) verify (and retry once if something is still holding the port)
powershell -NoProfile -Command "Start-Sleep -Milliseconds 400; $c = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 400 }; if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) { Write-Host '[Lydia] WARNING: port 8766 still in use.' } else { Write-Host '[Lydia] port 8766 clear.' }"

echo [Lydia] stopped.
timeout /t 2 /nobreak >nul
