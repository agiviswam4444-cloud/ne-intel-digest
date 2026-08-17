@echo off
setlocal
cd /d "%~dp0"
title Update NE Intel Digest
chcp 65001 >nul 2>nul
set PYTHONUTF8=1

echo ============================================================
echo   UPDATING from GitHub...
echo ============================================================
echo.

REM --- needs Git, and this folder must be a git clone (has a .git folder) ---
where git >nul 2>nul
if errorlevel 1 (
  echo [!] Git is not installed. Install "Git for Windows" from
  echo     https://git-scm.com/download/win  then run this again.
  pause
  exit /b 1
)
if not exist ".git" (
  echo [!] This folder was not set up from GitHub with git clone, so it
  echo     cannot auto-update. See README-WINDOWS.txt ^> "UPDATING" for the
  echo     one-time switch to a git clone.
  pause
  exit /b 1
)

git pull
if errorlevel 1 (
  echo.
  echo [!] Update failed ^(check your internet / GitHub sign-in^).
  pause
  exit /b 1
)

echo.
echo Update complete. Your collected news and settings are untouched.
echo Starting the app...
echo ============================================================
call start-windows.bat
