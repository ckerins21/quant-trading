@echo off
REM Activate virtual environment and pre-load cache
call .venv\Scripts\activate.bat
echo.
echo Fetching fresh market data and populating cache...
echo This will speed up the dashboard significantly.
echo.
python cache_loader.py --days 90
echo.
echo Cache pre-load complete! Now run: start_dashboard.bat
echo.
pause
