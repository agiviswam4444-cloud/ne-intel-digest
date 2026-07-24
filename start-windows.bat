@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title NE India Security Intel Digest

echo ============================================================
echo   NE INDIA // SECURITY INTEL DIGEST
echo ============================================================
echo.

REM --- 1. Check Python is installed ---
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python was not found on this PC.
  echo     Install Python 3.12 from https://www.python.org/downloads/
  echo     IMPORTANT: on the first install screen, tick
  echo     "Add python.exe to PATH", then run this file again.
  echo.
  pause
  exit /b 1
)

REM --- 2. First-run setup (dependencies + headless browser) ---
if not exist ".setup_done" (
  echo First-time setup - installing dependencies. This runs ONCE and
  echo may take a few minutes ^(downloads a headless browser, ~150 MB^).
  echo.
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  if errorlevel 1 ( echo [!] pip install failed. & pause & exit /b 1 )
  python -m playwright install chromium
  echo done> .setup_done
  echo.
  echo Setup complete.
  echo.
)

REM --- 3. Open the dashboard in the default browser shortly after startup ---
start "" cmd /c "timeout /t 10 >nul & start http://127.0.0.1:8642"

echo Starting the dashboard server...
echo   * Your browser will open automatically at http://127.0.0.1:8642
echo   * The first news collection takes 2-3 minutes; the page fills in
echo     by itself and then refreshes every 30 minutes.
echo   * KEEP THIS WINDOW OPEN. Close it to stop the app.
echo ============================================================
echo.
python run.py serve
pause
