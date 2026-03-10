@echo off
echo ========================================
echo   Smart Work Assistant - Startup
echo ========================================
echo.

cd /d "%~dp0"

:: Check if Python 3.12 is installed
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.12 is not installed or not in PATH
    echo Please install Python 3.12 from https://python.org
    pause
    exit /b 1
)

:: Check if venv exists, create if not
if not exist "backend\venv" (
    echo [INFO] Creating virtual environment with Python 3.12...
    cd backend
    py -3.12 -m venv venv
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

:: Kill any existing process on port 8000
echo [INFO] Checking for existing process on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [INFO] Killing existing process on port 8000 (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
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
