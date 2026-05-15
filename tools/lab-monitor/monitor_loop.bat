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
set "PYTHON=C:\Tools\venv\Scripts\python.exe"
set "SCRIPT=C:\Tools\ScanFlow\tools\lab-monitor\take_screenshots.py"
set "OUTPUT=C:\ScanflowMonitor\screenshots"
set "INTERVAL=300"
:: --------------------------------------------------------------------

if not exist "%PYTHON%" (
    echo [ERROR] Python venv not found at: %PYTHON%
    echo Edit this script and point PYTHON at your ScanFlow venv.
    pause
    exit /b 1
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
