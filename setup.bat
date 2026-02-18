@echo off
echo ========================================
echo   Smart Work Assistant - Setup
echo ========================================
echo.

cd /d "%~dp0"

:: Check Python
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed!
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)
python --version

:: Create venv
echo.
echo [2/4] Creating virtual environment...
if exist "backend\venv" (
    echo Virtual environment already exists, skipping...
) else (
    cd backend
    python -m venv venv
    cd ..
    echo Done!
)

:: Install dependencies
echo.
echo [3/4] Installing Python dependencies...
call backend\venv\Scripts\activate.bat
pip install -r backend\requirements.txt

:: Check Ollama
echo.
echo [4/4] Checking Ollama...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Ollama is not installed!
    echo.
    echo Please install Ollama from: https://ollama.ai
    echo Then run: ollama pull qwen2.5:7b
    echo.
) else (
    ollama --version
    echo.
    echo Pulling recommended model...
    ollama pull qwen2.5:7b
)

echo.
echo ========================================
echo   Setup Complete!
echo ========================================
echo.
echo Next steps:
echo   1. Start Ollama:  ollama serve
echo   2. Run the app:   run.bat
echo   3. Open browser:  http://localhost:8000
echo.
echo Optional: Configure Outlook integration
echo   - Edit backend\.env with your Azure credentials
echo.
pause
