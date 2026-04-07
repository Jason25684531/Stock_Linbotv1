# Proposal: setup-daily-automation

## 背景 (Context)

Stock Linbotv1 的每日營運流程（資料更新 → 選股 → LINE 推播 → Web 服務）目前需手動依序執行多個 Python 腳本，缺乏自動化排程機制。此外，前端 Dashboard 存在兩個顯示問題：

1. **即時選股訊號空白**：`1_update_database.py` 更新原始行情後，若未執行 `2_rundaily.py` 計算技術指標（MA/RSI/MACD 等），`daily_market_data` 的指標欄位全為 NULL，導致所有策略篩選出 0 筆候選。
2. **資產曲線圖表不渲染**：`renderChart()` 在 `loading = false` 之前執行，此時 canvas 容器處於 `display: none` 狀態，Chart.js 無法取得正確尺寸。
3. **即時選股訊號卡片 JS null 錯誤**：`x-show` 只控制 CSS 顯隱，但子元素的 `x-text` 表達式仍會被 Alpine.js 求值，對 null 值呼叫 `.toFixed()` 會產生 TypeError。

## 目標 (Objectives)

1. **建立 `daily_run.bat`**：一鍵依序執行 `1_update_database.py` → `2_rundaily.py` → `5_push_to_line.py`，含 UTF-8 編碼設定與日誌輸出。
2. **建立 `start_web.bat`**：使用 `pythonw.exe` 背景啟動 Flask Web 服務（無黑視窗），接收 LINE Webhook。
3. **註冊 Windows 工作排程器**：`Stock_Linbot_Daily` 排程每日 18:00 自動執行 `daily_run.bat`。
4. **修正前端顯示**：確認資產曲線圖表正確渲染、即時選股訊號正確顯示（含 null 安全防護）。

## 架構影響 (Architecture)

| 類別 | 影響檔案 | 說明 |
|------|---------|------|
| 新增 | `daily_run.bat` | 每日批次排程腳本 |
| 新增 | `start_web.bat` | 背景 Web 服務啟動腳本 |
| 修改 | `templates/dashboard.html` | 修復 renderChart 時序 + null 安全 |
| 系統 | Windows Task Scheduler | 新增 `Stock_Linbot_Daily` 排程 |

**不影響**：`app.py`、`tool/db_helper.py`、策略模組、測試。

## 重要備註

- 專案虛擬環境路徑為 `myenv\`（非 `venv\`），bat 腳本需使用 `myenv\Scripts\python.exe`。
- Flask 伺服器端口為 `1688`（非 5000）。
