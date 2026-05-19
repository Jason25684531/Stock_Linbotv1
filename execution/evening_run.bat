@echo off
REM Compatibility-only batch wrapper.
REM Official daily scheduler path: jobs\scheduler.py
REM Do not remove until cleanup evidence passes.
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe

echo ============================================================
echo  StockAI - 晚間資料庫更新與選股排程啟動
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] 階段 1/3: 正在從 MCP 抓取最新股價與法人籌碼...
%PYTHON% -X utf8 1_update_database.py

echo.
echo [%time%] 階段 2/3: 正在計算技術指標並執行策略過濾...
%PYTHON% -X utf8 2_rundaily.py

echo.
echo [%time%] 階段 3/3: 正在發送選股日報至 LINE...
%PYTHON% -X utf8 5_push_to_line.py --time evening

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
