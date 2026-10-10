@echo off
cd /d "%~dp0"
python -c "import pystray" 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install pystray Pillow --quiet
)
if "%1"=="widget" (
    python server.py --widget
) else if "%1"=="web" (
    python server.py --web-only
) else (
    python server.py
)
pause
