# V35 架構重構與 Line Bot 互動升級 (Refactor & Flex Message)

## 1. Context (背景)
目前的 V35 系統在「策略邏輯」與「執行引擎」之間存在耦合，導致回測 (`4_run_backtest.py`) 與實盤 (`2_rundaily.py`) 的出場邏輯可能不一致。同時，Line Bot (`app.py`) 僅支援純文字推播，缺乏互動性與資訊視覺化。

## 2. Objectives (目標)
1.  **邏輯解耦 (Decoupling)**：將交易出場規則（移動停損、最大持有天數）從回測引擎遷移至策略類別 (`BaseStrategy`)，實現「策略即邏輯」。
2.  **互動升級 (Interaction)**：讓使用者能輸入股票代號（如 `2330`），系統回傳包含 AI 評分、基本面數據的 **Line Flex Message (卡片訊息)**。

## 3. Implementation Plan (實作計畫)

### Phase 1: 策略邏輯物件化 (Strategy Decoupling)
- [x] **3.1 擴充 BaseStrategy 介面** (`tool/strategies/base.py`)
    - [x] 新增 `check_exit_signal(self, stock_id, current_price, current_date, position_info, market_trend)` 方法。
    - [x] 實作「階梯式移動停損 (Stepped Trailing Stop)」邏輯作為預設行為 (Level 1: 10%, Level 2: 20%, Level 3: 30%)。
    - [x] 實作 `max_hold_days` 檢查邏輯。
    - [x] 回傳格式標準化：`(action: str, reason: str, updated_stop_loss: float)`。

- [x] **3.2 重構回測引擎** (`4_run_backtest.py`)
    - [x] 移除 `run()` 迴圈內寫死的停損/停利 `if-else` 判斷區塊。
    - [x] 改為呼叫 `strategy.check_exit_signal()` 取得決策。
    - [x] 確保 `PortfolioBacktestEngine` 支援此新介面 — 提取 `check_and_execute_exit()` 共用方法。

- [x] **3.3 回歸測試 (Regression Testing)**
    - [x] 執行 `v33_low_vol` 回測，比對重構前後的 ROI 與交易次數是否一致（容許極小誤差）。
    - [x] 新增 `test/test_v35_refactor_flex.py` (15 test cases) 全數通過。

### Phase 2: Line Bot Flex Message 升級
- [x] **4.1 建立資料聚合工具** (`tool/report_helper.py`)
    - [x] 已有 `get_stock_report(stock_id)` 實作，聚合所有資料來源。
    - [x] 聚合來源：`daily_market_data` (收盤價/MA), `daily_recommendations` (AI Score), `financial_statements` (OpMg), `monthly_revenue` (YoY)。

- [x] **4.2 建立 Flex Message 建構器** (`tool/line_message_builder.py`)
    - [x] 使用 `linebot.v3.messaging.FlexMessage` (SDK v3)。
    - [x] 設計卡片佈局：
        - **Header**: 股票代號與趨勢標籤（🟢多頭/🔴空頭）。
        - **Hero**: 大字體顯示目前股價與漲跌幅。
        - **Body**: 顯示 AI 分數、RSI、營業利益率、營收 YoY、匹配策略。
        - **Footer**: Goodinfo 外部連結按鈕。

- [x] **4.3 更新 Line Bot 入口** (`app.py`)
    - [x] 修改 `handle_message` 邏輯。
    - [x] 當偵測到 4 位數股票代號時，呼叫 `report_helper` 與 `line_message_builder`。
    - [x] 回傳漂亮的 Flex Message 取代純文字，含 fallback 機制。

### Phase 3: 死碼清理 (Dead Code Cleanup)
- [x] `tool/strategy.py` 移除 ~130 行冗餘：`_load_v31_model()`, `check_sentiment_filter()`, `check_market_trend()`, `calculate_position_size()`
- [x] `get_best_stocks_v31_hybrid()` 改用 `db_helper.get_market_trend()` + `model_utils.load_model()`

## 4. Verification (驗證)
- [x] 執行 `python 4_run_backtest.py --v33_low_vol` 無報錯且績效正常。
- [x] 啟動 `python app.py`，對 Line Bot 輸入 "2330"，確認收到格式正確的卡片訊息。
- [x] `pytest` 全套測試 33/34 通過（1 個 MySQL 連線測試因本地無 DB 預期失敗）。