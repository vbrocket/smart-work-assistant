@echo off
echo ========================================
echo   Smart Work Assistant - Startup
echo ========================================
echo.

cd /d "%~dp0"

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Check if venv exists, create if not
if not exist "backend\venv" (
    echo [INFO] Creating virtual environment...
    cd backend
    python -m venv venv
    cd ..
)

:: Activate venv
echo [INFO] Activating virtual environment...
call backend\venv\Scripts\activate.bat

:: Check if fastapi is installed (key dependency)
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    python -m pip install --upgrade pip --quiet
    pip install -r backend\requirements.txt --quiet
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        echo Try deleting backend\venv folder and run again
        pause
        exit /b 1
    )
    echo [INFO] Dependencies installed successfully
)

:: Create data directory for database
if not exist "backend\data" (
    echo [INFO] Creating data directory...
    mkdir backend\data
)

:: Create logs directory
if not exist "backend\logs" (
    echo [INFO] Creating logs directory...
    mkdir backend\logs
)

:: Initialize and seed database if it doesn't exist
if not exist "backend\data\assistant.db" (
    echo [INFO] Initializing and seeding database...
    cd backend
    python init_db.py
    cd ..
) else (
    echo [INFO] Database already exists
)

:: Check if Ollama is running
echo [INFO] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Ollama is not running!
    echo Starting Ollama in background...
    start "" ollama serve
    timeout /t 3 >nul
)

:: Start the backend
echo.
echo ========================================
echo [INFO] Starting Smart Work Assistant...
echo [INFO] Open http://localhost:8000 in your browser
echo [INFO] Press Ctrl+C to stop
echo ========================================
echo.

cd backend
python main.py

pause
