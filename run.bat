@echo off
cd /d %~dp0

set PYTHON=C:\Users\zyx\.conda\envs\workbench\python.exe
set PORT=8001

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    pause
    exit /b 1
)

echo Starting server on port %PORT%...
echo Browser will open in 4 seconds...

start "" /b "%PYTHON%" -m uvicorn app:app --port %PORT%

timeout /t 4 /nobreak >nul

start "" http://localhost:%PORT%

echo.
echo Server running at http://localhost:%PORT%
echo Close this window to stop the server.
echo.

pause
