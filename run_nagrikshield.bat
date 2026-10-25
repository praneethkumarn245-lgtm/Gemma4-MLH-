@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the project virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Could not create the virtual environment. Install Python 3 and try again.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import flask, google.genai, PIL, reportlab, dotenv" >nul 2>&1
if errorlevel 1 (
    echo Installing application dependencies. This may take a few minutes...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo.
echo NagrikShield AI is starting.
echo Keep this window open while you use the app.
echo Open http://127.0.0.1:5000
echo Press Ctrl+C in this window to stop the server.
echo.
".venv\Scripts\python.exe" app.py
pause
