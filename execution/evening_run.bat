@echo off
REM Compatibility-only batch wrapper.
REM Official daily scheduler path: jobs\scheduler.py
REM Do not remove until cleanup evidence passes.
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe
set SCHEDULER=jobs\scheduler.py
REM change 2026-11-09-market-data-autonomy (decision.md D1/D2): trusted grant file outside the repo, listed-share scope, scheduled trigger
set STOCK_LINBOT_GRANTS=C:\ProgramData\StockLinbot\grants\owner_grants.json
set STOCK_LINBOT_MARKET_SCOPE=TWSE_ONLY
set STOCK_LINBOT_TRIGGER=scheduled

echo ============================================================
echo  StockAI - 晚間資料庫更新與選股排程啟動
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] 正在執行 canonical evening scheduler...
%PYTHON% -X utf8 %SCHEDULER% evening --stop-on-error
set "SCHEDULER_EXIT=%ERRORLEVEL%"

if not "%SCHEDULER_EXIT%"=="0" (
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
exit /b %SCHEDULER_EXIT%
