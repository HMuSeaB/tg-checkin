$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$py = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $py)) { $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path $py)) {
    Write-Host '未找到 .venv，请先执行:' -ForegroundColor Red
    Write-Host '  python -m venv .venv' -ForegroundColor Yellow
    Write-Host '  .venv\Scripts\pip install -r requirements.txt' -ForegroundColor Yellow
    exit 1
}

Write-Host '正在启动签到面板...' -ForegroundColor Green
Start-Process -FilePath $py -ArgumentList (Join-Path $PSScriptRoot 'app.py') -WorkingDirectory $PSScriptRoot
Start-Sleep -Seconds 3
Start-Process 'http://127.0.0.1:8899'
