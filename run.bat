@echo off
setlocal
title KANSLOR Crime

cd /d "%~dp0"

echo ==========================================
echo              KANSLOR CRIME
echo ==========================================
echo.

REM ---- Check Python ----
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3 was not found.
    echo.
    echo Install Python 3 and enable "Add Python to PATH",
    echo then run this launcher again.
    echo.
    pause
    exit /b 1
)

REM ---- Windows curses support ----
python -c "import curses" >nul 2>&1
if errorlevel 1 (
    echo First-time setup: installing Windows terminal support...
    python -m pip install windows-curses

    if errorlevel 1 (
        echo.
        echo [ERROR] Could not install windows-curses.
        pause
        exit /b 1
    )
    echo.
)

REM ---- Bootstrap/update public data ----
echo Updating SAPD call data...
python collect.py

if errorlevel 1 (
    echo.
    echo [WARNING] Data update failed.
    echo Crime will attempt to open using existing local data.
    echo.
)

REM ---- Launch UI ----
echo Starting Crime...
echo.

python crime_ui.py

if errorlevel 1 (
    echo.
    echo [ERROR] Crime exited with an error.
    pause
)

endlocal