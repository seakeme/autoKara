@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM   autoKara launcher entry  (ASCII-only on purpose so Chinese
REM   Windows cmd parses it cleanly without UTF-8 BOM issues)
REM
REM   1) check if private Python is present
REM   2) if not, the user closed the install-time config window
REM      -> auto-resume setup_env.bat
REM   3) start the GUI via pythonw + launcher.py
REM ============================================================

set "PY=%~dp0python\pythonw.exe"

if not exist "%PY%" (
    echo.
    echo ============================================================
    echo   autoKara environment not configured yet.
    echo   Resuming setup now: download ~3-5 GB, takes 10-40 minutes.
    echo   *** Do NOT close this window until you see "Setup complete" ***
    echo ============================================================
    echo.
    call "%~dp0setup_env.bat"
    if not exist "%PY%" (
        echo.
        echo Setup did not complete. You can re-run it from the
        echo Start Menu shortcut "Reconfigure environment".
        pause
        exit /b 1
    )
)

start "" "%PY%" "%~dp0launcher.py"
