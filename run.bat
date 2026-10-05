@echo off
rem ==================================================================
rem  IV4 Data Agent - one command
rem    run                 setup (first time) + checks + start
rem    run status          is it running? today's numbers
rem    run check           preflight checks only
rem    run metrics ...     production metrics      run help  = all commands
rem  Double-click works too.
rem ==================================================================
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

rem --- find Python 3.10+ (prefer the py launcher; skip the Microsoft Store stub)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
    echo.
    echo  Python 3.10 or newer was not found.
    echo  Install it from https://www.python.org/downloads/windows/
    echo  and tick "Add python.exe to PATH", then run this again.
    echo.
    pause
    exit /b 9009
)

rem --- first-run setup: .venv, dependencies (only when changed), .env
set "DEV="
if /i "%~1"=="test" set "DEV=--dev"
set "VPY="
for /f "delims=" %%P in ('%PY% "%~dp0scripts\bootstrap.py" %DEV%') do set "VPY=%%P"
if not defined VPY (
    echo.
    echo  Setup failed - see the message above.
    pause
    exit /b 1
)

rem --- run
"%VPY%" -m app %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if "%~1"=="" (
    echo.
    echo  Exit code %RC% - window kept open so you can read the message.
    pause
)
exit /b %RC%
