# Tasks: setup-daily-automation

## Phase 1：修復前端顯示問題（`templates/dashboard.html`）

- [x] **1.1 修復資產曲線 `renderChart()` 渲染時序**
  - 檔案：`templates/dashboard.html` `init()` 函式
  - 將 `renderChart()` 移至 `this.loading = false` 之後，包在 `this.$nextTick(() => { ... })` 中
  - 原因：canvas 容器在 `x-show="!loading"` 下為 `display: none`，Chart.js 無法取得尺寸
  - 驗收：啟動 `python app.py`，開啟 `http://localhost:1688/dashboard`，確認資產曲線圖表正常顯示

- [x] **1.2 修復即時選股訊號卡片 null 安全**
  - 檔案：`templates/dashboard.html` 信號卡片區塊（rsi / volume / ma20 / ma60 / op_profit_margin / revenue_yoy 欄位）
  - 對所有 `x-text` 中的 `.toFixed()` 呼叫加上 `!= null ?` 三元判斷
  - 原因：`x-show` 只控制 CSS，Alpine.js 仍會求值子元素的 `x-text`，null 值呼叫 `.toFixed()` 會產生 TypeError
  - 驗收：瀏覽器 DevTools Console 無 `TypeError: Cannot read properties of null` 錯誤

- [x] **1.3 驗證前後端 API 嫁接正確**
  - 啟動 Flask，驗證以下 API 回應：
    - `GET /api/performance`：回傳 `dates`/`equity`/`roi` 三個陣列，長度 > 0
    - `GET /api/daily-signals?strategy=v38_value_dividend&top_n=5`：回傳 `signals` 陣列長度 > 0，`date` 為今日
    - `GET /api/trades`：回傳非空陣列
  - 驗收：`curl` 三個端點均回傳合法 JSON、無 500 錯誤

---

## Phase 2：建立每日執行批次檔（`daily_run.bat`）

- [x] **2.1 建立 `daily_run.bat`**
  - 路徑：專案根目錄 `D:\01_Project\Stocke\Stock_Linbotv1\daily_run.bat`
  - 規格：
    1. `chcp 65001` 設定 UTF-8 編碼
    2. `cd /d D:\01_Project\Stocke\Stock_Linbotv1` 切換至專案目錄
    3. 使用 `myenv\Scripts\python.exe` 依序執行：
       - `1_update_database.py`（資料更新）
       - `2_rundaily.py`（選股計算）
       - `5_push_to_line.py`（LINE 推播）
    4. 每步驟前後加 `echo` 日誌提示，含時間戳
    5. 最末使用 `pause` 或自動關閉（排程用途建議不 pause）
  - 驗收：雙擊 `daily_run.bat`，三個腳本依序執行完成，無亂碼

---

## Phase 3：建立背景 Web 服務腳本（`start_web.bat`）

- [x] **3.1 建立 `start_web.bat`**
  - 路徑：專案根目錄 `D:\01_Project\Stocke\Stock_Linbotv1\start_web.bat`
  - 規格：
    1. `cd /d D:\01_Project\Stocke\Stock_Linbotv1` 切換至專案目錄
    2. 使用 `start "" myenv\Scripts\pythonw.exe app.py` 背景啟動 Flask
    3. `pythonw.exe` 不開啟終端機視窗
  - 驗收：雙擊 `start_web.bat`，無黑色視窗出現；瀏覽器訪問 `http://localhost:1688/dashboard` 可正常載入

---

## Phase 4：註冊 Windows 工作排程器

- [x] **4.1 透過 `schtasks` 建立每日排程**
  - 排程名稱：`Stock_Linbot_Daily`
  - 觸發條件：每天 18:00
  - 執行動作：`D:\01_Project\Stocke\Stock_Linbotv1\daily_run.bat`
  - 起始位置：`D:\01_Project\Stocke\Stock_Linbotv1`
  - 驗收：執行 `schtasks /query /tn Stock_Linbot_Daily` 確認排程已建立

- [x] **4.2 權限檢查與回報**
  - 若 `schtasks` 因權限不足失敗，提供以系統管理員身分執行的指引
  - 驗收：排程狀態為 `Ready`

---

## Phase 5：整合驗收

- [x] **5.1 完整端對端驗證**
  - 啟動 `start_web.bat` → 瀏覽器確認 Dashboard 載入正常
  - 確認：
    - [x] 資產曲線圖表正常渲染（非空白區塊）
    - [x] 即時選股訊號顯示今日資料（日期為 2026-03-06 或最新交易日）
    - [x] 策略切換後信號正確更新
    - [x] 瀏覽器 Console 無 JS 錯誤

- [x] **5.2 回歸測試**
  - 執行 `pytest` 確認所有測試通過：**117/117 passed** ✅
