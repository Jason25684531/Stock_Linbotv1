@echo off
REM Compatibility-only batch wrapper.
REM Official daily scheduler path: jobs\scheduler.py
REM Do not remove until cleanup evidence passes.
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe

echo ============================================================
echo  StockAI - 早晨新聞摘要推播啟動
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] 正在執行新聞彙整與推播...
%PYTHON% -X utf8 jobs\scheduler.py morning

if %errorlevel% neq 0 (
    echo.
    echo [%time%] ❌ 執行失敗，請檢查網路或 Gemini API Key。
) else (
    echo.
    echo [%time%] ✅ 早晨推播任務已成功完成！
)

echo.
echo ============================================================
echo  視窗將於 10 秒後自動關閉...
timeout /t 10 >nul
