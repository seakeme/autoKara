@echo off
setlocal enabledelayedexpansion
title autoKara setup

REM ============================================================
REM   autoKara environment setup  (ASCII-only English on purpose:
REM   Chinese Windows cmd does not reliably strip a UTF-8 BOM,
REM   so a BOM file fails to parse. Keep this file pure ASCII.)
REM
REM   Resumable staged installer:
REM     1. private Python 3.11
REM     2. pip upgrade
REM     3. torch / torchaudio  (CUDA chosen from driver version)
REM     4. requirements.txt
REM     5. pre-download MMS-FA, Demucs, nltk cmudict
REM
REM   Each phase writes its own .state\*.ok marker; re-run skips
REM   completed phases.
REM
REM   Env var:  AUTOKARA_MIRROR=cn  -> TUNA + Aliyun + HF-mirror
REM ============================================================

set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "PYDIR=%APPDIR%\python"
set "PY=%PYDIR%\python.exe"
set "MARKER=%APPDIR%\.env_ready"
set "STATEDIR=%APPDIR%\.state"
if not exist "%STATEDIR%" mkdir "%STATEDIR%" 2>nul

set "M_PY=%STATEDIR%\python.ok"
set "M_PIP=%STATEDIR%\pip.ok"
set "M_TORCH=%STATEDIR%\torch.ok"
set "M_DEPS=%STATEDIR%\deps.ok"
set "M_MODELS=%STATEDIR%\models.ok"

set "PY_VER=3.11.9"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "PY_SETUP=%TEMP%\autokara-python-%PY_VER%.exe"

set "PIP_EXTRA="
if /i "%AUTOKARA_MIRROR%"=="cn" (
    set "PIP_EXTRA=-i https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://mirrors.aliyun.com/pypi/simple"
    set "HF_ENDPOINT=https://hf-mirror.com"
    echo [mirror] AUTOKARA_MIRROR=cn  using TUNA + Aliyun + HF-mirror
)

echo.
echo ============================================================
echo.
echo     ***  DO NOT CLOSE THIS WINDOW  ***
echo.
echo     autoKara is configuring its runtime environment.
echo     This takes 10-40 minutes (network dependent).
echo     Closing this window now leaves the install incomplete.
echo     It will auto-resume next time you launch autoKara,
echo     so re-running is safe -- but be patient.
echo.
echo ============================================================
echo.
echo   Install dir : %APPDIR%
echo.

if exist "%MARKER%" (
    echo [skip] Environment already fully configured.
    echo        To reinstall: delete %MARKER%
    echo        To redo one phase: delete the matching .state\*.ok
    goto :done
)

REM ---------- 1/5  private Python ----------
if exist "%M_PY%" if exist "%PY%" (
    echo [1/5 SKIP] private Python already in place
    goto :step2
)
echo [1/5] Downloading Python %PY_VER% ...
where curl >nul 2>&1
if !errorlevel!==0 (
    curl -L -# -o "%PY_SETUP%" "%PY_URL%"
) else (
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%'"
)
if not exist "%PY_SETUP%" (
    echo [ERROR] Python download failed. Check network and re-run this script.
    goto :fail
)
echo       Installing to %PYDIR%  (private, does not touch system PATH)...
"%PY_SETUP%" /quiet TargetDir="%PYDIR%" Include_tcltk=1 Include_pip=1 Include_test=0 PrependPath=0 AssociateFiles=0 Shortcuts=0 Include_launcher=0 InstallAllUsers=0
if not exist "%PY%" (
    echo [ERROR] Python install failed.
    goto :fail
)
del "%PY_SETUP%" >nul 2>&1
echo done>"%M_PY%"

:step2
REM ---------- 2/5  pip ----------
if exist "%M_PIP%" (
    echo [2/5 SKIP] pip already upgraded
    goto :step3
)
echo [2/5] Upgrading pip ...
"%PY%" -m pip install --upgrade pip %PIP_EXTRA%
if !errorlevel! neq 0 goto :fail
echo done>"%M_PIP%"

:step3
REM ---------- 3/5  torch (driver-based CUDA selection) ----------
if exist "%M_TORCH%" (
    echo [3/5 SKIP] torch / torchaudio already installed
    goto :step4
)
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
set "GPU=CPU"
set "DRV_MAJ=0"
where nvidia-smi >nul 2>&1
if !errorlevel!==0 (
    for /f "tokens=*" %%v in ('nvidia-smi --query-gpu^=driver_version --format^=csv^,noheader 2^>nul') do set "DRV=%%v"
    if defined DRV (
        for /f "tokens=1 delims=." %%a in ("!DRV!") do set "DRV_MAJ=%%a"
        if !DRV_MAJ! GEQ 570 (
            set "TORCH_INDEX=https://download.pytorch.org/whl/cu128"
            set "GPU=CUDA cu128  (Blackwell-ready, driver !DRV!)"
        ) else (
            if !DRV_MAJ! GEQ 525 (
                set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
                set "GPU=CUDA cu121  (driver !DRV!)"
            ) else (
                set "GPU=CPU  (NVIDIA driver !DRV! is too old, need 525+)"
            )
        )
    )
)
echo [3/5] Installing torch / torchaudio   [!GPU!]
echo       The CUDA wheel is large (~2-3 GB). Please wait, do not close.
"%PY%" -m pip install torch torchaudio --index-url !TORCH_INDEX!
if !errorlevel! neq 0 goto :fail
echo done>"%M_TORCH%"

:step4
REM ---------- 4/5  remaining requirements ----------
if exist "%M_DEPS%" (
    echo [4/5 SKIP] dependencies already installed
    goto :step5
)
echo [4/5] Installing dependencies (requirements.txt) ...
"%PY%" -m pip install %PIP_EXTRA% -r "%APPDIR%\requirements.txt"
if !errorlevel! neq 0 goto :fail
echo done>"%M_DEPS%"

:step5
REM ---------- 5/5  pre-download models and dictionaries ----------
if exist "%M_MODELS%" (
    echo [5/5 SKIP] models / dictionaries already cached
    goto :finish
)
echo [5/5] Installing models and dictionaries (first run will be offline-ready)...
echo       - cmudict (English pronunciation dict; bundled, no download)
REM cmudict ships inside the installer (corpora\cmudict.zip). We copy it into the
REM private Python's nltk_data search path so NO network is needed -- the GitHub
REM download used to hang for users behind slow/blocked connections. cmudict is
REM optional anyway (only English words in lyrics use it), so any failure just warns.
if exist "%APPDIR%\nltk_data\corpora\cmudict.zip" (
    if not exist "%PYDIR%\nltk_data\corpora" mkdir "%PYDIR%\nltk_data\corpora" 2>nul
    copy /y "%APPDIR%\nltk_data\corpora\cmudict.zip" "%PYDIR%\nltk_data\corpora\cmudict.zip" >nul
)
"%PY%" -c "from nltk.corpus import cmudict; cmudict.dict(); print('  cmudict OK (bundled)')"
if !errorlevel! neq 0 (
    REM bundled copy missing/unreadable -> one time-boxed download attempt (never hangs)
    echo       bundled copy not found, trying a quick download (max ~25s)...
    "%PY%" -c "import socket; socket.setdefaulttimeout(25); import nltk; nltk.download('cmudict')" >nul 2>&1
    "%PY%" -c "from nltk.corpus import cmudict; cmudict.dict(); print('  cmudict OK (downloaded)')"
    if !errorlevel! neq 0 (
        echo       [warn] cmudict unavailable. OPTIONAL -- autoKara still works;
        echo              English words just use approximate romaji.
    )
)
echo       - Demucs htdemucs_ft  (~300 MB)
"%PY%" -c "from demucs.pretrained import get_model; get_model('htdemucs_ft'); print('  Demucs OK')"
if !errorlevel! neq 0 goto :fail
echo       - MMS-FA forced-alignment model  (~1.26 GB)
"%PY%" -c "import torchaudio; torchaudio.pipelines.MMS_FA.get_model(); print('  MMS-FA OK')"
if !errorlevel! neq 0 goto :fail
echo done>"%M_MODELS%"

:finish
echo done>"%MARKER%"
echo.
echo ============================================================
echo   Setup complete.  Launch autoKara from the Desktop or
echo   Start Menu shortcut.
echo ============================================================
goto :done

:fail
echo.
echo ============================================================
echo   Setup failed at one of the steps.  Common causes:
echo     - unstable network
echo     - antivirus blocking python install / pip
echo     - GPU driver too old
echo   Re-run this script -- completed phases will be skipped.
echo   If you are on a slow international link, set:
echo       set AUTOKARA_MIRROR=cn  ^&^&  setup_env.bat
echo   to use China mirrors.
echo ============================================================
pause
exit /b 1

:done
exit /b 0
