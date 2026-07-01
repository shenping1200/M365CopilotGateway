@echo off
setlocal
cd /d %~dp0
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
echo Starting unified M365 API on http://0.0.0.0:8080/v1
echo API Key: any value is accepted
echo.
python -X utf8 unified_server.py
