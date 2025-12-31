@echo off
REM ============================================
REM Stock AI Line Bot - 服務啟動批次檔
REM 版本: V3.0 (V30策略增強版)
REM 用途: 啟動 Flask Line Bot 服務
REM ============================================

REM 設定專案目錄
cd /d "d:\01_Project\Stocke\Stock_Linbotv1"

echo ============================================
echo   Stock AI Line Bot V3.0
echo   啟動 Flask 服務中...
echo ============================================

REM 啟動虛擬環境
call .\myenv\Scripts\activate.bat

REM 啟動 Line Bot
python app.py

REM 服務結束後
echo.
echo Line Bot 服務已停止
pause
