@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
set "PYW=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\pythonw.exe"
set "LOG=%~dp0desktop_manager_start.log"

if not exist "%PY%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python was not found. > "%LOG%"
    echo [ERROR] Python was not found. Please install Python 3.13 or add Python to PATH.
    pause
    exit /b 1
  )
  set "PY=python"
)

"%PY%" -m py_compile "%~dp0gui_launcher_v2.py" "%~dp0app_launcher.py" "%~dp0server.py" "%~dp0auth.py" "%~dp0copilot_client.py" > "%LOG%" 2>&1
if errorlevel 1 (
  type "%LOG%"
  echo.
  echo [ERROR] ????????????%LOG%
  pause
  exit /b 1
)

if exist "%PYW%" (
  start "M365 Copilot Manager" "%PYW%" "%~dp0gui_launcher_v2.py"
) else (
  start "M365 Copilot Manager" "%PY%" "%~dp0gui_launcher_v2.py"
)
exit /b 0
