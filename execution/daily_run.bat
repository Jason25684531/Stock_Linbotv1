@echo off
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1

echo ============================================================
echo  Stock Linbot - 每日自動化排程
echo  執行時間: %date% %time%
echo ============================================================

echo.
echo [%time%] ========== Step 1: 更新資料庫 (1_update_database.py) ==========
myenv\Scripts\python.exe -X utf8 1_update_database.py
if %errorlevel% neq 0 (
    echo [%time%] ❌ Step 1 失敗，錯誤碼: %errorlevel%
) else (
    echo [%time%] ✅ Step 1 完成
)

echo.
echo [%time%] ========== Step 2: 每日選股 (2_rundaily.py) ==========
myenv\Scripts\python.exe -X utf8 2_rundaily.py
if %errorlevel% neq 0 (
    echo [%time%] ❌ Step 2 失敗，錯誤碼: %errorlevel%
) else (
    echo [%time%] ✅ Step 2 完成
)

echo.
echo [%time%] ========== Step 3: LINE 推播 (5_push_to_line.py) ==========
myenv\Scripts\python.exe -X utf8 5_push_to_line.py
if %errorlevel% neq 0 (
    echo [%time%] ❌ Step 3 失敗，錯誤碼: %errorlevel%
) else (
    echo [%time%] ✅ Step 3 完成
)

echo.
echo ============================================================
echo  全部執行完畢: %date% %time%
echo ============================================================
