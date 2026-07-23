@echo off
REM Compatibility-only batch wrapper.
REM Official daily scheduler path: jobs\scheduler.py
REM Do not remove until cleanup evidence passes.
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe
set SCHEDULER=jobs\scheduler.py

echo ============================================================
echo  StockAI - 晚間資料庫更新與選股排程啟動
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] 正在執行 canonical evening scheduler...
%PYTHON% -X utf8 %SCHEDULER% evening --stop-on-error

if %errorlevel% neq 0 (
    echo.
    echo [%time%] ❌ 執行過程中出錯，請檢查 MCP Server 是否有開啟。
) else (
    echo.
    echo [%time%] ✅ 晚間所有自動化任務已成功跑完！
)

echo.
echo ============================================================
echo  視窗將於 10 秒後自動關閉...
timeout /t 10 >nul
