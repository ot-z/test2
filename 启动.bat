@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 刷题学习助手

echo.
echo   ============================================================
echo      刷题学习助手 - 正在启动本地服务
echo   ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [初始化] 未检测到虚拟环境，正在创建并安装依赖，请稍候...
    python -m venv .venv
    if errorlevel 1 goto :failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" run.py
if errorlevel 1 goto :failed
goto :end

:failed
echo.
echo   启动失败，请把上面的错误信息发给我排查。
echo.

:end
echo.
echo   服务已退出。按任意键关闭窗口...
pause >nul
