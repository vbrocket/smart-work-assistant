@echo off
REM setup-new-server.bat — Upload snapshot + setup script to a new vast.ai server and run it
REM
REM Usage:  setup-new-server.bat HOST PORT
REM   e.g.  setup-new-server.bat 109.231.106.68 45859
REM

set SSH_KEY=%USERPROFILE%\.ssh\vast_ed25519

if "%~1"=="" (
    set /p VAST_IP="Enter server IP: "
    set /p VAST_PORT="Enter SSH port: "
) else (
    set VAST_IP=%~1
    set VAST_PORT=%~2
    if "%~2"=="" set VAST_PORT=22
)

set VAST_HOST=root@%VAST_IP%
set SCP_CMD=scp -i "%SSH_KEY%" -P %VAST_PORT%
set SSH_CMD=ssh -i "%SSH_KEY%" -p %VAST_PORT%

echo.
echo ============================================================
echo   Smart Work Assistant — New Server Setup
echo ============================================================
echo.
echo   Server: %VAST_HOST% (port %VAST_PORT%)
echo.

REM Check files exist
if not exist "pwa-server-snapshot.tar.gz" (
    echo ERROR: pwa-server-snapshot.tar.gz not found in current directory
    echo Run this from the project root where the snapshot file is.
    pause
    exit /b 1
)

if not exist "setup-new-server.sh" (
    echo ERROR: setup-new-server.sh not found in current directory
    pause
    exit /b 1
)

echo [1/3] Uploading snapshot (8-9 MB) and setup script...
%SCP_CMD% pwa-server-snapshot.tar.gz setup-new-server.sh %VAST_HOST%:/workspace/
echo    Upload complete.

echo [2/3] Fixing line endings and setting permissions...
%SSH_CMD% %VAST_HOST% "sed -i 's/\r$//' /workspace/setup-new-server.sh && chmod +x /workspace/setup-new-server.sh"

echo [3/3] Running setup (this will take 10-30 min for model downloads)...
echo.
echo ---- Server output below ----
echo.
%SSH_CMD% -o ServerAliveInterval=30 -o ServerAliveCountMax=60 %VAST_HOST% "cd /workspace && ./setup-new-server.sh"

echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
echo   SSH tunnel:  ssh -i %SSH_KEY% -p %VAST_PORT% %VAST_HOST% -L 18000:localhost:18000 -N
echo   Then open:   http://localhost:18000
echo.
pause
