# 生成「TG签到面板」快捷方式（双击启动，无黑框）
# 用法：右键 -> 使用 PowerShell 运行，或在项目根执行
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $py)) {
    $py = Join-Path $root '.venv\Scripts\python.exe'
}
if (-not (Test-Path $py)) {
    Write-Host '未找到 .venv，请先执行:' -ForegroundColor Red
    Write-Host '  python -m venv .venv' -ForegroundColor Yellow
    Write-Host '  .venv\Scripts\pip install -r requirements.txt' -ForegroundColor Yellow
    exit 1
}

$app = Join-Path $root 'app.py'
$ico = Join-Path $root 'app.ico'
$lnk = Join-Path $root 'TG签到面板.lnk'

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $py
$s.Arguments = '"' + $app + '"'
$s.WorkingDirectory = $root
if (Test-Path $ico) { $s.IconLocation = $ico + ',0' }
$s.Description = 'TG 每日签到面板'
$s.WindowStyle = 7
$s.Save()

Write-Host ('已生成快捷方式: ' + $lnk) -ForegroundColor Green
Write-Host '把它拖到桌面或任务栏即可，双击直接开面板，不会有黑框。' -ForegroundColor Cyan
