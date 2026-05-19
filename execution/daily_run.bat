@echo off
REM Compatibility-only batch wrapper.
REM Official daily scheduler path: jobs\scheduler.py daily
REM Do not remove until cleanup evidence passes.
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe

echo ============================================================
echo  Stock Linbot - 每日自動化排程
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] ========== Scheduler: jobs/scheduler.py daily ==========
%PYTHON% -X utf8 jobs\scheduler.py daily
if %errorlevel% neq 0 (
    echo [%time%] ❌ 排程失敗，錯誤碼: %errorlevel%
) else (
    echo [%time%] ✅ 排程完成
)

echo.
echo ============================================================
echo  全部執行完畢: %date% %time%
echo ============================================================
