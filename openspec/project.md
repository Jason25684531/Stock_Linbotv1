# Project Specification: Stock_Linbotv1 Strategy Upgrade (V33/V34)

## 1. 專案目標 (Project Goal)
將現有的 Python 量化交易腳本 (`Stock_Linbotv1`) 重構為模組化的 **「策略工廠模式 (Strategy Factory Pattern)」**。目標是建立一個能夠支援多策略切換（穩健/動能）的系統，並解決營收資料抓取困難的問題，以實現高報酬的 V34 策略。

## 2. 核心技術棧 (Core Technologies)
- **Language**: Python 3.10+
- **Database**: SQLite (`daily_market_data.db`)
- **Data Source**: 
  - Price: TWSE/TPEX Daily Report.
  - Revenue: **`mopsov.twse.com.tw`** (Backup site for stability).
- **Web Dashboard**: Flask (MVC Architecture).
- **Notification**: Line Bot API (Dynamic message content).

## 3. 架構設計 (Architecture)
系統採用 **MVC 架構**：
- **Model (Strategy Logic)**: `BaseStrategy` 抽象類別，衍生出 V31, V33, V34。
- **Controller (Manager)**: `StrategyManager` 負責讀取 `settings.json` 並實例化當前策略。
- **View (Web)**: Flask Dashboard 提供下拉選單切換策略。

## 4. 策略定義 (Strategies)
1.  **V31 Hybrid (現行版)**:
    - 邏輯：MA + RSI + 籌碼面。
    - 目標：平衡型波段。
2.  **V33 Low Volatility (低波動穩健型)**:
    - 邏輯：`NATR < 4%`, `STD_20` 低。
    - 目標：降低 MDD，追求月獲利 3~5%。
3.  **V34 Twin-Turbo (雙渦輪飆股型)**:
    - 邏輯：**營收 YoY > 30%** (關鍵) + 股價創 60 日新高。
    - 目標：追求月獲利 10% 以上 (高風險高報酬)。

## 5. 關鍵數據需求 (Data Requirements)
- 必須修復月營收爬蟲，取得 `revenue_yoy` 欄位，否則 V34 無法運作。
- 必須計算 `natr` 與 `std_20` 指標，否則 V33 無法運作。