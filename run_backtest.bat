@echo off
REM ============================================
REM Stock AI - 回測執行批次檔
REM 版本: V31 (統一回測引擎)
REM ============================================

cd /d "d:\01_Project\Stocke\Stock_Linbotv1"

echo ============================================
echo   V31 策略回測
echo ============================================
echo.
echo 用法:
echo   run_backtest.bat        預設 V31 模式
echo   run_backtest.bat v30    純技術面 V30
echo   run_backtest.bat v31    混合策略 V31
echo.

call .\myenv\Scripts\activate.bat

if "%1"=="v30" (
    python 4_run_backtest.py --v30
) else (
    python 4_run_backtest.py --v31
)

echo.
echo 回測完成
pause
