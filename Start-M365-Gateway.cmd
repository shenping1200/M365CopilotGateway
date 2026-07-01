@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo  M365 Copilot Gateway
echo  API:       http://127.0.0.1:8080/v1
echo  Dashboard: http://127.0.0.1:7860
echo  Mode:      supervised auto-restart
echo ========================================
echo.
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0m365_supervisor.py"
pause
