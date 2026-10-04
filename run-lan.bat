@echo off
cd /d "%~dp0"
set STOCK_DASHBOARD_HOST=0.0.0.0
where py >nul 2>nul && (py -3 app.py %*) || (python app.py %*)
pause
