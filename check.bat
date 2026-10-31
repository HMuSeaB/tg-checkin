@echo off
chcp 65001 >nul
title TG 签到面板 - 环境自检
cd /d "%~dp0"

echo ============================================================
echo   TG 签到面板 环境自检
echo ============================================================
echo.

set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" (
    echo [X] 找不到 .venv\Scripts\python.exe
    goto :fail
)

echo [1/4] 检查 Python 能否启动...
"%PY%" -V
if errorlevel 1 (
    echo.
    echo [X] Python 启动失败。
    echo     最常见原因：杀毒软件（联想电脑管家 / 火绒 / 360）拦截了 python.exe
    echo     处理办法：把下面两个目录加入杀软的「信任区 / 排除项」，然后重试
    echo        %~dp0.venv
    echo        D:\Python
    goto :fail
)

echo.
echo [2/4] 检查依赖...
"%PY%" -c "import telethon, fastapi, uvicorn, apscheduler, python_socks; print('    依赖齐全')"
if errorlevel 1 (
    echo     [X] 依赖缺失，正在安装...
    "%~dp0.venv\Scripts\pip.exe" install -r requirements.txt
)

echo.
echo [3/4] 检查桌面端会话...
"%PY%" -c "import os; p='config/tg_session.session'; print('    已有会话文件' if os.path.exists(p) else '    需要生成会话')"

echo.
echo [4/4] 启动面板...
start "" "%~dp0run.bat"
echo.
echo 若浏览器没自动打开，手动访问 http://127.0.0.1:8899
pause
exit /b 0

:fail
echo.
echo 自检未通过，请按上面的提示处理后重试。
pause
exit /b 1
