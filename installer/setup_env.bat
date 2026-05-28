@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title autoKara 环境配置

rem ============================================================
rem  autoKara 环境配置脚本（在目标机器、安装时自动运行）
rem  本文件以 UTF-8 + BOM 保存，Windows 10/11 cmd.exe 据此按 UTF-8 解析。
rem
rem  分阶段标记 (.state\*.ok)：每一步成功后写入对应标记。再次运行时
rem  已完成的步骤直接跳过 —— 任意一步失败、断网或被杀都能续跑。
rem
rem  环境变量：
rem    AUTOKARA_MIRROR=cn   → 启用国内镜像 (PyPI tuna + aliyun, HF-mirror)
rem
rem  阶段：
rem    1. 私有 Python (3.11.9)
rem    2. pip 升级
rem    3. torch / torchaudio (按 NVIDIA 驱动版本自动选 cu128 / cu121 / cpu)
rem    4. 其余依赖 (requirements.txt)
rem    5. 预下载模型 (MMS-FA, Demucs, nltk cmudict)
rem ============================================================

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

rem 国内镜像：AUTOKARA_MIRROR=cn
set "PIP_EXTRA="
if /i "%AUTOKARA_MIRROR%"=="cn" (
    set "PIP_EXTRA=-i https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://mirrors.aliyun.com/pypi/simple"
    set "HF_ENDPOINT=https://hf-mirror.com"
    echo [镜像] AUTOKARA_MIRROR=cn 已启用国内镜像 (PyPI tuna/aliyun, HF-mirror)
)

echo(
echo ============================================================
echo
echo     [!] 请勿关闭此窗口   DO NOT CLOSE THIS WINDOW
echo
echo     autoKara 正在配置运行环境（10-40 分钟，取决于网速）。
echo     看到 "环境配置完成" 字样后窗口才会自动结束。
echo     若中途意外关闭，下次启动 autoKara 会自动续装。
echo
echo ============================================================
echo(
echo   安装目录: %APPDIR%
echo(

if exist "%MARKER%" (
    echo [跳过] 环境已完整配置。如需重装：
    echo        - 删除标记文件 %MARKER%
    echo        - 或单步重跑：删除对应 %STATEDIR%\*.ok 即可
    goto :done
)

rem ---------- 1/5  私有 Python ----------
if exist "%M_PY%" if exist "%PY%" (
    echo [1/5 SKIP] 私有 Python 已就绪
    goto :step2
)
echo [1/5] 下载 Python %PY_VER% ...
where curl >nul 2>&1
if !errorlevel!==0 (
    curl -L -# -o "%PY_SETUP%" "%PY_URL%"
) else (
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%'"
)
if not exist "%PY_SETUP%" (
    echo [错误] Python 下载失败。请检查网络后重新运行本脚本。
    goto :fail
)
echo       正在静默安装到 %PYDIR% （独立环境，不修改系统 PATH）...
"%PY_SETUP%" /quiet TargetDir="%PYDIR%" Include_tcltk=1 Include_pip=1 Include_test=0 PrependPath=0 AssociateFiles=0 Shortcuts=0 Include_launcher=0 InstallAllUsers=0
if not exist "%PY%" (
    echo [错误] Python 安装失败。
    goto :fail
)
del "%PY_SETUP%" >nul 2>&1
echo done>"%M_PY%"

:step2
rem ---------- 2/5  pip ----------
if exist "%M_PIP%" (
    echo [2/5 SKIP] pip 已升级
    goto :step3
)
echo [2/5] 升级 pip ...
"%PY%" -m pip install --upgrade pip %PIP_EXTRA%
if !errorlevel! neq 0 goto :fail
echo done>"%M_PIP%"

:step3
rem ---------- 3/5  torch（按 NVIDIA 驱动版本选 cu128 / cu121 / cpu） ----------
if exist "%M_TORCH%" (
    echo [3/5 SKIP] torch / torchaudio 已安装
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
            set "GPU=CUDA cu128 (Blackwell-ready, driver !DRV!)"
        ) else (
            if !DRV_MAJ! GEQ 525 (
                set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
                set "GPU=CUDA cu121 (driver !DRV!)"
            ) else (
                set "GPU=CPU (NVIDIA driver !DRV! 过旧，需升至 525+)"
            )
        )
    )
)
echo [3/5] 安装 torch / torchaudio  [!GPU!]
echo       CUDA 版下载量较大（约 2-3GB），请耐心等待，勿关闭窗口...
"%PY%" -m pip install torch torchaudio --index-url !TORCH_INDEX!
if !errorlevel! neq 0 goto :fail
echo done>"%M_TORCH%"

:step4
rem ---------- 4/5  其余依赖 ----------
if exist "%M_DEPS%" (
    echo [4/5 SKIP] 依赖已安装
    goto :step5
)
echo [4/5] 安装其余依赖 (requirements.txt) ...
"%PY%" -m pip install %PIP_EXTRA% -r "%APPDIR%\requirements.txt"
if !errorlevel! neq 0 goto :fail
echo done>"%M_DEPS%"

:step5
rem ---------- 5/5  预下载模型与词典 ----------
if exist "%M_MODELS%" (
    echo [5/5 SKIP] 模型与词典已就绪
    goto :finish
)
echo [5/5] 预下载模型与词典（首次启动即可离线使用）...
echo       - nltk cmudict
"%PY%" -m nltk.downloader cmudict
if !errorlevel! neq 0 goto :fail
echo       - Demucs htdemucs_ft 人声分离模型 (~300MB)
"%PY%" -c "from demucs.pretrained import get_model; get_model('htdemucs_ft'); print('  Demucs OK')"
if !errorlevel! neq 0 goto :fail
echo       - MMS-FA 强制对齐模型 (~1.26GB)
"%PY%" -c "import torchaudio; torchaudio.pipelines.MMS_FA.get_model(); print('  MMS-FA OK')"
if !errorlevel! neq 0 goto :fail
echo done>"%M_MODELS%"

:finish
echo done>"%MARKER%"
echo(
echo ============================================================
echo   环境配置完成！可从开始菜单 / 桌面快捷方式启动 autoKara。
echo ============================================================
goto :done

:fail
echo(
echo ============================================================
echo   环境配置在某一步失败。常见原因：网络不稳定 / 显卡驱动 / 杀毒拦截。
echo   修复后直接重新运行本脚本即可 —— 已完成的步骤会自动跳过。
echo   国内网络环境建议先设置环境变量再运行：
echo       set AUTOKARA_MIRROR=cn  ^&^&  setup_env.bat
echo ============================================================
pause
exit /b 1

:done
exit /b 0
