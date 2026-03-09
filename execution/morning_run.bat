@echo off
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1

echo ============================================================
echo  Stock Linbot - 早晨大局觀推播
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] ========== 早晨推播 (morning) ==========
D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe -X utf8 5_push_to_line.py --time morning
if %errorlevel% neq 0 (
    echo [%time%] ❌ 早晨推播失敗，錯誤碼: %errorlevel%
) else (
    echo [%time%] ✅ 早晨推播完成
)

echo.
echo ============================================================
echo  執行完畢: %date% %time%
echo ============================================================
