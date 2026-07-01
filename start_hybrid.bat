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
python -m pip install --upgrade pip >nul 2>&1
if exist requirements.txt python -m pip install -r requirements.txt >nul 2>&1
python -m pip install fastapi uvicorn pydantic requests websockets python-dotenv httpx >nul 2>&1
echo Starting M365 Hybrid Gateway on http://0.0.0.0:8000/v1
echo LAN clients use: http://^<THIS_IP^>:8000/v1
echo.
python -X utf8 -m uvicorn hybrid_server:app --host 0.0.0.0 --port 8000 --reload
