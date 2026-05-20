@echo off
:: =====================================================================
::  pull_logs.bat
::
::  Copy ScanFlow's rolling daily log files from the lab PC into this
::  dev PC's WSL filesystem, where the developer can read them. Run
::  on the DEV PC after a session at the rig.
::
::  Setup once on the LAB PC (PowerShell as Administrator):
::      New-SmbShare -Name scanflow_logs -Path "C:\ScanflowMonitor\logs" `
::                   -ReadAccess "Everyone"
::  Then run this .bat from the dev PC.
::
::  Files land under \\wsl.localhost\Ubuntu-24.04\... so the WSL-side
::  developer can grep / cat / paste them into chat directly.
:: =====================================================================

set "SRC=\\SMP-8HSN6L3\scanflow_logs"
set "DST=\\wsl.localhost\Ubuntu-24.04\home\xperiment\Playground-SPMQTLab\ScanFlow\lab-logs"

:: --- Sanity --------------------------------------------------------
if not exist "%SRC%" (
    echo [ERROR] Cannot reach the lab log share: %SRC%
    echo Confirm 'scanflow_logs' is shared on the lab PC and credentials
    echo are cached ^(cmdkey /add:SMP-8HSN6L3 /user:ltspm /pass:...^).
    pause
    exit /b 1
)

echo Pulling logs from %SRC%
echo                 to %DST%
echo.

robocopy "%SRC%" "%DST%" ^
    *.log *.txt ^
    /S /XO ^
    /R:2 /W:3 /NP ^
    /LOG+:"%TEMP%\scanflow_pull_logs.log"

set RC=%ERRORLEVEL%
if %RC% LSS 4 (
    echo.
    echo [OK]  Pulled cleanly ^(robocopy code %RC%^).
    echo Logs are now visible to WSL at:
    echo   /home/xperiment/Playground-SPMQTLab/ScanFlow/lab-logs/
) else (
    echo.
    echo [FAIL] robocopy exit code %RC% — see %TEMP%\scanflow_pull_logs.log
)

pause
