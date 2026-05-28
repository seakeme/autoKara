@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

rem ============================================================
rem  autoKara 入口脚本（安装后桌面/开始菜单快捷方式指向这里）
rem  作用：
rem    1. 检查私有 Python 是否就绪
rem    2. 如果环境还没配（用户在安装时关掉了配置窗口），自动接着配
rem    3. 启动 GUI
rem  本文件以 UTF-8 + BOM 保存，让 Windows cmd 正确解析中文。
rem ============================================================

set "PY=%~dp0python\pythonw.exe"

if not exist "%PY%" (
    echo.
    echo ============================================================
    echo   autoKara 还没装完运行环境，现在自动接着装。
    echo   下载约 3-5 GB，10-40 分钟（取决于网速 + 显卡）。
    echo   *** 请保持此窗口开着，别关 ***
    echo ============================================================
    echo.
    call "%~dp0setup_env.bat"
    if not exist "%PY%" (
        echo.
        echo 环境配置未完成。可在开始菜单运行 "重新配置环境" 再试。
        pause
        exit /b 1
    )
)

start "" "%PY%" "%~dp0launcher.py"
