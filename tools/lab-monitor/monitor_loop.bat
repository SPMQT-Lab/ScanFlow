@echo off
:: =====================================================================
::  monitor_loop.bat
::
::  Runs the screenshot polling loop on the LAB PC. Drops one trio of
::  PNGs (desktop + ScanFlow + STMAFM windows) into the shared folder
::  every INTERVAL seconds.
::
::  Edit the four variables below for your setup, then double-click.
::  Close the CMD window to stop.
::
::  For unattended operation (survives log-off / reboot), register this
::  with Windows Task Scheduler with --once and a trigger every N
::  minutes — see tools/lab-monitor/README.md.
:: =====================================================================

:: --- Configuration ---------------------------------------------------
:: Lab PC paths (user 'ltspm', ScanFlow synced to Desktop\scanflow).
:: PYTHON is the system Python (no venv); ensure 'pillow' and 'pywin32'
:: are installed there with:
::     pip install pillow pywin32
set "PYTHON=python"
set "SCRIPT=C:\Users\ltspm\Desktop\scanflow\tools\lab-monitor\take_screenshots.py"
set "OUTPUT=C:\ScanflowMonitor\screenshots"
set "INTERVAL=300"
:: --------------------------------------------------------------------

:: When PYTHON is a bare command (e.g. "python") we let PATH resolve it
:: and check via `where`. When it's a full path, fall back to `if exist`.
where "%PYTHON%" >nul 2>&1
if errorlevel 1 (
    if not exist "%PYTHON%" (
        echo [ERROR] Python not found on PATH or at: %PYTHON%
        echo Either install Python or edit PYTHON= in this script.
        pause
        exit /b 1
    )
)
if not exist "%SCRIPT%" (
    echo [ERROR] take_screenshots.py not found at: %SCRIPT%
    pause
    exit /b 1
)

echo ScanFlow monitor — capturing every %INTERVAL%s
echo Output: %OUTPUT%
echo Press Ctrl-C to stop.
echo.

"%PYTHON%" "%SCRIPT%" --output "%OUTPUT%" --interval %INTERVAL%
