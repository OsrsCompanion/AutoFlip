@echo off
cd /d C:\osrs-flip-assistant\backend

echo Starting backend...
start cmd /k py -m uvicorn app.main:app --host 0.0.0.0 --port 8000

timeout /t 3 >nul

echo Starting Cloudflare tunnel...
start cmd /k cloudflared tunnel --url http://localhost:8000

echo.
echo ===============================
echo Backend + Tunnel started
echo ===============================
echo.
pause