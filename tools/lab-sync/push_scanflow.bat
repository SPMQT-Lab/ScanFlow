@echo off
:: =====================================================================
::  push_scanflow.bat
::
::  Sync the local ScanFlow source folder to the lab PC over the LAN.
::  Run on the DEV PC. Lab PC must expose a writable SMB share that
::  contains the target ScanFlow checkout.
::
::  Edit the two paths below, then double-click this file (or run it
::  from CMD). It mirrors *only* the scanflow/ package + tools/ +
::  pyproject.toml — never the .git folder, virtualenv, caches, or
::  __pycache__ dirs. So the lab PC's editable install picks up the
::  changes immediately with no pip reinstall.
::
::  Safety: uses /E (copy + add) not /MIR — existing lab-PC files outside
::  the source tree are never deleted. To genuinely mirror (i.e. also
::  delete files that no longer exist on dev), change /E to /MIR below.
:: =====================================================================

set "SRC=C:\Tools\ScanFlow"
set "DST=\\LABPC\Tools\ScanFlow"

:: --- Sanity: source must exist ---------------------------------------
if not exist "%SRC%" (
    echo [ERROR] Source folder not found: %SRC%
    pause
    exit /b 1
)

:: --- Sanity: target reachable ----------------------------------------
if not exist "%DST%" (
    echo [ERROR] Target share not reachable: %DST%
    echo Check the lab PC is on, the share is exported, and you're on the LAN.
    pause
    exit /b 1
)

echo Syncing %SRC%  -^>  %DST%
echo (Excludes: .git, __pycache__, .pytest_cache, *.pyc, venv folders)
echo.

robocopy "%SRC%" "%DST%" ^
    /E ^
    /XD .git __pycache__ .pytest_cache .ruff_cache .venv venv build dist *.egg-info ^
    /XF *.pyc *.pyo *.log .DS_Store Thumbs.db ^
    /R:2 /W:3 /NP ^
    /LOG+:"%TEMP%\scanflow_push.log"

set RC=%ERRORLEVEL%
:: robocopy exit codes 0..3 are success; 4+ indicates real failure.
if %RC% LSS 4 (
    echo.
    echo [OK]  Sync completed cleanly ^(robocopy code %RC%^).
    echo Full log: %TEMP%\scanflow_push.log
) else (
    echo.
    echo [FAIL] robocopy exit code %RC% — see %TEMP%\scanflow_push.log
)

pause
