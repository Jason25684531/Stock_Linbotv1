# Stock AI Line Bot V3.0 - PowerShell Launcher
# 解決編碼問題並啟動 Flask 應用程式

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$Host.UI.RawUI.OutputEncoding = [System.Text.Encoding]::UTF8
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Stock AI Line Bot V3.0" -ForegroundColor Green
Write-Host " 正在啟動 Flask 服務..." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 切換到腳本所在目錄
Set-Location $PSScriptRoot

# 啟動虛擬環境並執行應用程式
& ".\myenv\Scripts\python.exe" app.py

Write-Host ""
Write-Host "服務已停止" -ForegroundColor Red
pause
