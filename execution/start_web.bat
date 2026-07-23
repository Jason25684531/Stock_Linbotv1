@echo off
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe
set PYTHONW=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\pythonw.exe

echo 啟動 MCP 服務: services\mcp\server.py
start "Stock Linbot MCP" %PYTHON% -X utf8 services\mcp\server.py

echo 啟動 Web + Line Bot: app.py
start "Stock Linbot Web" %PYTHONW% app.py
