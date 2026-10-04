@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 app.py --demo) || (python app.py --demo)
pause
