@echo off
chcp 65001 >nul
set "REPO_ROOT=D:\01_Project\Stocke\Stock_Linbotv1"
cd /d "%REPO_ROOT%"
set "PYTHON=%REPO_ROOT%\myenv\Scripts\python.exe"
set "PYTHONW=%REPO_ROOT%\myenv\Scripts\pythonw.exe"

echo 啟動 MCP 服務: services\mcp\server.py
start "Stock Linbot MCP" %PYTHON% -X utf8 "%REPO_ROOT%\services\mcp\server.py"

echo 啟動 Web + Line Bot: app.py
start "Stock Linbot Web" %PYTHONW% "%REPO_ROOT%\app.py"
