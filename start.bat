@echo off
title arXivScooper
echo Starting arXivScooper server...
echo.
cd /d "%~dp0"
python -m scq serve %*
if errorlevel 1 (
    echo.
    echo Failed to start. Either Python is not installed, or the scq package
    echo is not on this Python's path. From the repo root, run:
    echo     pip install -e .
    echo (one-time setup; then double-click START.bat again).
    echo.
    pause
)
