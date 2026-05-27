@echo off
:: ============================================================
:: install_probeflow.bat
:: Run this once from the lab PC to install ProbeFlow into the
:: same Python environment that ScanFlow uses.
::
:: Expected folder layout:
::   Playground-SPMQTLab\
::       ScanFlow\          <-- this script lives here
::       ProbeFlow\         <-- sibling, will be installed -e
::       venv\              <-- venv here OR inside ScanFlow\
:: ============================================================

setlocal enabledelayedexpansion

:: ── Locate this script's directory (= ScanFlow root) ──────────────────────
set "SCANFLOW_DIR=%~dp0"
:: Strip trailing backslash so we can do clean path joins
if "%SCANFLOW_DIR:~-1%"=="\" set "SCANFLOW_DIR=%SCANFLOW_DIR:~0,-1%"

:: Parent of ScanFlow = Playground-SPMQTLab
for %%I in ("%SCANFLOW_DIR%") do set "BUNDLE_DIR=%%~dpI"
if "%BUNDLE_DIR:~-1%"=="\" set "BUNDLE_DIR=%BUNDLE_DIR:~0,-1%"

set "PROBEFLOW_DIR=%BUNDLE_DIR%\ProbeFlow"

echo.
echo ScanFlow dir : %SCANFLOW_DIR%
echo Bundle dir   : %BUNDLE_DIR%
echo ProbeFlow dir: %PROBEFLOW_DIR%
echo.

:: ── Verify ProbeFlow exists ────────────────────────────────────────────────
if not exist "%PROBEFLOW_DIR%\pyproject.toml" (
    echo ERROR: ProbeFlow not found at %PROBEFLOW_DIR%
    echo        Make sure the ProbeFlow folder is next to ScanFlow.
    goto :fail
)

:: ── Find pip ───────────────────────────────────────────────────────────────
:: Priority: 1) venv next to ScanFlow   2) venv inside ScanFlow   3) system pip

set "PIP="

if exist "%BUNDLE_DIR%\venv\Scripts\pip.exe" (
    set "PIP=%BUNDLE_DIR%\venv\Scripts\pip.exe"
    echo Using venv: %BUNDLE_DIR%\venv
    goto :run
)

if exist "%SCANFLOW_DIR%\venv\Scripts\pip.exe" (
    set "PIP=%SCANFLOW_DIR%\venv\Scripts\pip.exe"
    echo Using venv: %SCANFLOW_DIR%\venv
    goto :run
)

:: Fall back to system pip
where pip >nul 2>&1
if %errorlevel%==0 (
    set "PIP=pip"
    echo WARNING: no venv found — installing into system Python.
    echo          If that is not what you want, activate your venv first
    echo          and re-run this script.
    echo.
) else (
    echo ERROR: No pip found. Activate your venv or install Python first.
    goto :fail
)

:run
echo Installing ProbeFlow in editable mode...
echo.
"%PIP%" install -e "%PROBEFLOW_DIR%"

if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed ^(exit code %errorlevel%^).
    goto :fail
)

echo.
echo ============================================================
echo  ProbeFlow installed successfully.
echo  ScanFlow will find it automatically on next launch.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
