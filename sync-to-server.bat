@echo off
REM sync-to-server.bat — Compress, upload, and extract code on vast.ai (fast)
REM
REM Usage:  sync-to-server.bat [HOST] [PORT]
REM   e.g.  sync-to-server.bat 109.231.106.68 45859
REM         sync-to-server.bat              (uses last saved or prompts)
REM
REM Requires: ssh + scp + tar (all built into Windows 10+)

REM ── Configuration ──────────────────────────────────────────────────────
set VAST_DIR=/workspace/pwa-idea
set SSH_KEY=%USERPROFILE%\.ssh\vast_ed25519
set LOCAL_DIR=%~dp0
set SERVER_CACHE=%LOCAL_DIR%.vast_server

REM Try command-line args first, then cached file, then prompt
if NOT "%~1"=="" (
    set VAST_IP=%~1
    set VAST_PORT=%~2
    if "%~2"=="" set VAST_PORT=22
) else if exist "%SERVER_CACHE%" (
    for /f "tokens=1,2" %%a in (%SERVER_CACHE%) do (
        set VAST_IP=%%a
        set VAST_PORT=%%b
    )
) else (
    set /p VAST_IP="Enter server IP: "
    set /p VAST_PORT="Enter SSH port: "
)

REM Save for next time
echo %VAST_IP% %VAST_PORT%> "%SERVER_CACHE%"
set VAST_HOST=root@%VAST_IP%

set SCP_CMD=scp -i "%SSH_KEY%" -P %VAST_PORT%
set SSH_CMD=ssh -i "%SSH_KEY%" -p %VAST_PORT%
set ARCHIVE=pwa-sync.tar.gz

echo.
echo ============================================================
echo   Smart Work Assistant — Sync to Vast.ai
echo ============================================================
echo.
echo   Server: %VAST_HOST% (port %VAST_PORT%)
echo   Local:  %LOCAL_DIR%
echo   Remote: %VAST_DIR%
echo.

REM ── Step 1: Compress ───────────────────────────────────────────────────
echo [1/4] Compressing project files...
cd /d "%LOCAL_DIR%"
tar --exclude="__pycache__" --exclude="*.pyc" --exclude=".env" --exclude="backend/data" --exclude="logs" --exclude="*.db" --exclude=".git" --exclude="node_modules" --exclude=".deps_installed" --exclude="documents" --exclude="*.tar.gz" --exclude="venv" --exclude="*.wav" --exclude="*.pdf" --exclude="chroma_db" -czf "%TEMP%\%ARCHIVE%" backend frontend data/data sync-to-server.sh
echo    Compressed to %TEMP%\%ARCHIVE%

REM ── Step 2: Upload single archive ─────────────────────────────────────
echo [2/4] Uploading archive (single file transfer)...
%SCP_CMD% "%TEMP%\%ARCHIVE%" %VAST_HOST%:/tmp/%ARCHIVE%
echo    Upload complete.

REM ── Step 3: Extract on server + restart ────────────────────────────────
echo [3/4] Extracting on server and restarting app...
%SSH_CMD% %VAST_HOST% "mkdir -p %VAST_DIR% && tar -xzf /tmp/%ARCHIVE% -C %VAST_DIR% && rm /tmp/%ARCHIVE% && find %VAST_DIR%/backend -name '*.sh' -exec sed -i 's/\r$//' {} + && chmod +x %VAST_DIR%/backend/*.sh && echo '   Files extracted + CRLF fixed + scripts chmod +x'"

echo [4/4] Restarting app...
%SSH_CMD% %VAST_HOST% "cd %VAST_DIR%/backend && pkill -f 'uvicorn main:app' 2>/dev/null; sleep 2; rm -rf services/__pycache__ routers/__pycache__; mkdir -p logs && nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 18000 --workers 1 --log-level info > logs/app.log 2>&1 & echo App started PID=$!"

REM ── Cleanup local temp ─────────────────────────────────────────────────
del "%TEMP%\%ARCHIVE%" 2>nul

echo.
echo ============================================================
echo   Sync complete! App restarted on server.
echo ============================================================
echo.
echo   Access via tunnel: http://localhost:18000
echo     ssh -i vast_ed25519 -p %VAST_PORT% %VAST_HOST% -L 18000:localhost:18000 -N
echo.
pause
