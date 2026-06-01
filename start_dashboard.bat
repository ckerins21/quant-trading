@echo off
cd /d "C:\Users\caola\Documents\quant-trading"

:: Kill any old server already on port 8000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Pre-load cache before starting dashboard
echo.
echo ========================================
echo Updating market data cache...
echo ========================================
call .venv\Scripts\python.exe cache_loader.py --days 90
echo.
echo ========================================
echo Updating research data cache...
echo ========================================
call .venv\Scripts\python.exe cache_research_loader.py --days 30
echo.
echo ========================================
echo Cache updated. Starting dashboard...
echo ========================================
echo.

:: Start the server minimised in the background
start "QuantDash" /min .venv\Scripts\python.exe run_dashboard.py

:: Wait for it to be ready (polls until port 8000 responds)
:wait
timeout /t 1 /nobreak >nul
curl -s http://localhost:8000 >nul 2>&1
if errorlevel 1 goto wait

:: Open the browser
start "" "http://localhost:8000"
