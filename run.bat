@echo off
chcp 65001 >nul
title TG 签到面板
cd /d "%~dp0"

set PY=%~dp0.venv\Scripts\pythonw.exe
if not exist "%PY%" set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" (
    echo [ERROR] 未找到 .venv，请先执行:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo 正在启动签到面板...
start "" "%PY%" "%~dp0app.py"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8899"
exit /b 0
