$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\scripts\bootstrap_db.py"
if ($LASTEXITCODE -ne 0) { throw '数据库启动失败' }
& "$PSScriptRoot\.venv\Scripts\python.exe" -m quant.cli init-db
if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }
Write-Host '打开 http://127.0.0.1:8765 ，按 Ctrl+C 停止界面服务。'
& "$PSScriptRoot\.venv\Scripts\python.exe" -m quant.cli serve
