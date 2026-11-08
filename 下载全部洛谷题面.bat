@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Quiz Helper - Luogu Problem Download

echo.
echo   ============================================================
echo      Luogu public problem statements downloader (FR-30)
echo   ============================================================
echo.
echo   Default: full catalog (~16.7k problems), about 5-6 hours
echo            at 1.2s per request. Public pages only, no login.
echo   Press Ctrl+C to stop; run again to resume from checkpoint.
echo.
echo   PowerShell requires the .\ prefix for this file.
echo   Arguments are passed through, e.g.:
echo     --only-status             show progress
echo     --max-items 500           fetch next 500 problems
echo     --start 1001 --end 1100   fetch a range
echo     --rebuild-index           rebuild index.csv offline
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] .venv not found. Please run the setup script first.
    echo.
    pause
    exit /b 1
)

if "%~1"=="" goto :default
echo %* | findstr /i /c:"--start" /c:"--existing" /c:"--all" /c:"--catalog" /c:"--only-status" /c:"--rebuild-index" >nul
if errorlevel 1 goto :append_all
".venv\Scripts\python.exe" scripts\fetch_luogu.py %*
goto :check

:append_all
".venv\Scripts\python.exe" scripts\fetch_luogu.py --all --interval 1.2 %*
goto :check

:default
".venv\Scripts\python.exe" scripts\fetch_luogu.py --all --interval 1.2
goto :check

:check
if errorlevel 1 goto :failed
goto :end

:failed
echo.
echo   Download stopped or failed. Progress is saved; run again to resume.
echo.

:end
echo.
echo   Press any key to close...
pause >nul
