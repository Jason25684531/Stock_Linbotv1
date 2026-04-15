@echo off
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe

echo ============================================================
echo  Stock Linbot - 早晨大局觀推播
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] ========== Scheduler: jobs/scheduler.py morning ==========
%PYTHON% -X utf8 jobs\scheduler.py morning
if %errorlevel% neq 0 (
    echo [%time%] ❌ 早晨推播失敗，錯誤碼: %errorlevel%
) else (
    echo [%time%] ✅ 早晨推播完成
)

echo.
echo ============================================================
echo  執行完畢: %date% %time%
echo ============================================================
