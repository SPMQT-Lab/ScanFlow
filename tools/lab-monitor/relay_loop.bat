@echo off
:: pushd maps UNC paths (e.g. \\wsl.localhost\...) to a temp drive letter
:: so CMD can use it as the working directory.
pushd "%~dp0"
:: =====================================================================
::  relay_loop.bat
::
::  Runs the Slack/Telegram relay on the DEV PC. Watches the lab PC's
::  shared screenshot folder and forwards any new PNGs to Slack.
::
::  Requires env vars already set (permanent via setx or system props):
::      SLACK_BOT_TOKEN   xoxb-...
::      SLACK_CHANNEL     C0B486W8A7M
::
::  Edit the variables below if needed, then double-click.
::  Close the CMD window to stop.
:: =====================================================================

:: --- Configuration ---------------------------------------------------
set "PYTHON=python"
set "SCRIPT=relay_to_slack.py"
set "FOLDER=\\SMP-8HSN6L3\scanflow\screenshots"
set "INTERVAL=60"
set "DELAY=3"
:: State file: written to %%TEMP%% (always a valid Windows path — avoids
:: UNC/WSL write failures that silently break state between restarts).
set "STATE=%TEMP%\scanflow_relay_state.json"
:: --------------------------------------------------------------------

where "%PYTHON%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    pause
    exit /b 1
)
if not exist "%SCRIPT%" (
    echo [ERROR] relay_to_slack.py not found in: %CD%
    pause
    exit /b 1
)

:: Authenticate to the lab PC share root (not the subfolder)
net use "\\SMP-8HSN6L3\scanflow" /user:SMP-8HSN6L3\ltspm ltspm >nul 2>&1

echo ScanFlow relay — forwarding new screenshots to Slack every %INTERVAL%s
echo Folder : %FOLDER%
echo Press Ctrl-C to stop.
echo.

echo State  : %STATE%
echo.
"%PYTHON%" "%SCRIPT%" --folder "%FOLDER%" --interval %INTERVAL% --delay %DELAY% --state "%STATE%"
pause
