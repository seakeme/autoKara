@echo off
setlocal
cd /d "%~dp0"
title Build autoKara installer

REM ============================================================
REM   Build autoKara installer  ->  Output\autoKara-setup.exe
REM
REM   ASCII-only on purpose: Chinese in .bat files breaks Chinese
REM   Windows cmd.exe when the file lacks a UTF-8 BOM. This script
REM   is dev-only so English is fine here. The user-facing
REM   setup_env.bat preserves Chinese via UTF-8 BOM.
REM ============================================================

echo ============================================================
echo   Build autoKara installer  (Output\autoKara-setup.exe)
echo ============================================================
echo.

REM ---------- Generate app.ico from ..\knm.png if missing ----------
if exist "app.ico" (
    echo [1/2] app.ico already exists, skipping icon generation.
) else (
    echo [1/2] Generating app.ico from ..\knm.png ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Add-Type -AssemblyName System.Drawing; $img=[System.Drawing.Image]::FromFile((Resolve-Path '..\knm.png')); $bmp=New-Object System.Drawing.Bitmap($img,64,64); $h=$bmp.GetHicon(); $ico=[System.Drawing.Icon]::FromHandle($h); $fs=[System.IO.File]::Open((Join-Path (Get-Location) 'app.ico'),'Create'); $ico.Save($fs); $fs.Close(); $bmp.Dispose(); $img.Dispose(); Write-Host '      icon ok' } catch { Write-Host ('      skipped: ' + $_.Exception.Message) }"
)

REM ---------- Locate ISCC.exe (Inno Setup 7 / 6 / 5, both Program Files dirs) ----------
set "ISCC="
if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 5\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
if not defined ISCC for /f "delims=" %%I in ('where iscc 2^>nul') do set "ISCC=%%I"

if not defined ISCC (
    echo.
    echo [ERROR] ISCC.exe not found. Install Inno Setup 6 or 7 first:
    echo         https://jrsoftware.org/isdl.php
    echo     or: winget install JRSoftware.InnoSetup
    pause
    exit /b 1
)

echo [2/2] Using compiler: %ISCC%
echo       Compiling installer.iss ...
"%ISCC%" "installer.iss"
if errorlevel 1 (
    echo.
    echo [ERROR] Compilation failed. See Inno Setup output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Build succeeded!  Installer at:
echo     %~dp0Output\autoKara-setup.exe
echo   Distribute that .exe file to your users.
echo ============================================================
pause
