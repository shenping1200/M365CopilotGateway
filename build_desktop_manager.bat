@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo  M365 Copilot Gateway - Release Build
echo  onedir / windowed / no account data
echo ========================================
echo.

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing pyinstaller...
  "%PY%" -m pip install pyinstaller
  if errorlevel 1 goto :fail
)

echo [1/5] Syntax check...
"%PY%" -m py_compile runtime_config.py unified_server.py dashboard.py app_launcher.py m365_supervisor.py gui_launcher_v2.py auth.py copilot_client.py request_logger.py
if errorlevel 1 goto :fail

echo [2/5] Clean old build...
if exist build rmdir /s /q build
if exist dist\M365CopilotGateway rmdir /s /q dist\M365CopilotGateway

set ICON_ARG=
if exist app.ico set ICON_ARG=--icon app.ico

echo [3/5] Build onedir package...
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --onedir ^
  --windowed ^
  --name M365CopilotGateway ^
  %ICON_ARG% ^
  --add-data "app.ico;." ^
  --add-data "m365_runtime_config.json;." ^
  --add-data "C:\Users\Administrator\Desktop\M365\.venv\Lib\site-packages\safehttpx\version.txt;safehttpx" ^
  --add-data "C:\Users\Administrator\Desktop\M365\.venv\Lib\site-packages\groovy\version.txt;groovy" ^
  --collect-all safehttpx ^
  --collect-all groovy ^
  --collect-all gradio ^
  --collect-all gradio_client ^
  --collect-all PyQt5 ^
  --collect-all PyQtWebEngine ^
  --collect-all msal ^
  --collect-all websockets ^
  --collect-all httpx ^
  gui_launcher_v2.py
if errorlevel 1 goto :fail

echo [4/5] Create clean writable files...
if not exist dist\M365CopilotGateway\accounts.json echo []> dist\M365CopilotGateway\accounts.json
if exist app.ico copy /y app.ico dist\M365CopilotGateway\app.ico >nul
if exist dist\M365CopilotGateway\token_cache.json del /f /q dist\M365CopilotGateway\token_cache.json
if exist dist\M365CopilotGateway\substrate_token.txt del /f /q dist\M365CopilotGateway\substrate_token.txt
if exist dist\M365CopilotGateway\msal_cache.bin del /f /q dist\M365CopilotGateway\msal_cache.bin

echo [5/5] Sensitive file scan...
if exist dist\M365CopilotGateway\token_cache.json goto :fail_sensitive
if exist dist\M365CopilotGateway\substrate_token.txt goto :fail_sensitive
if exist dist\M365CopilotGateway\msal_cache.bin goto :fail_sensitive

echo.
echo [OK] Build complete: dist\M365CopilotGateway\M365CopilotGateway.exe
echo [OK] Account data was not packaged. A blank accounts.json was created.
exit /b 0

:fail_sensitive
echo [ERROR] Sensitive runtime file found in package.
exit /b 2

:fail
echo [ERROR] Build failed.
exit /b 1
