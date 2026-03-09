# 專案規格：Stock_Linbotv1（當前版本 V38）

> 最後更新：2026-03-09

## 1. 專案目標

**Stock_Linbotv1** 是一套整合台股量化分析、機器學習選股與 Line Bot 推播通知的自動化交易輔助系統。

核心目標：
- **穩健獲利**：透過多策略框架（保守型 / 動能型 / 價值型）因應不同市場環境，維持長期正期望值。
- **自動化營運**：每日自動更新資料、執行選股、推播建議，降低人工干預。
- **架構可擴展**：策略工廠模式（Strategy Factory Pattern）支援快速新增或切換策略，不影響核心引擎。

## 2. 核心技術棧

| 層次 | 技術 |
|------|------|
| 語言 | Python 3.10+ |
| 資料庫 | MySQL 8.0+（SQLAlchemy ORM，連線池 + 重試） |
| 資料來源 | TWSE/TPEX 每日行情、MOPS 月營收/季報爬蟲、融資融券爬蟲 |
| Web 儀表板 | Flask + Jinja2 + Tailwind CSS (CDN) + Plotly |
| 通知 | Line Bot SDK v3（Flex Message 卡片 + 廣播推播） |
| 機器學習 | XGBoost（每策略獨立模型，LRU 快取載入） |
| 參數最佳化 | Optuna（TPE Sampler） |

## 3. 系統架構

採用 **分層架構 + 策略工廠** 設計：

```
┌─────────────────────────────────────────────────────────┐
│                  應用層 (Application)                     │
│   app.py (Flask + Line Bot Webhook, port 1688)          │
│   execution/morning_run.bat (08:30 早晨大局觀)            │
│   execution/evening_run.bat (19:00 晚間選股策劃)          │
│   1~6_*.py (資料更新/選股/訓練/回測/推播/最佳化)          │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│         呈現層 (Presentation)                            │
│   templates/ (dashboard, backtest, login)                │
│   tool/line_message_builder.py (Flex Message 卡片)       │
│   tool/viz_helper.py (Plotly 互動式圖表)                 │
│   tool/report_helper.py (個股 AI 診斷報告)               │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                 策略層 (Multi-Strategy)                   │
│   tool/strategy_manager.py (Singleton 工廠, 7 策略註冊)   │
│   tool/strategies/base.py (BaseStrategy + check_exit)    │
│   tool/strategies/v31~v38 (各策略實作)                    │
│   tool/strategy.py (V30/V31 向後相容層)                   │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   指標層 (Indicators)                     │
│   tool/calc_indicators.py                                │
│   (MA, RSI, MACD, KD, BB, ATR, NATR, chip_score 等)     │
│   tool/model_utils.py (XGBoost 模型載入, LRU 快取)       │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   資料層 (Data)                           │
│   tool/db_helper.py (唯一 DB 存取入口)                    │
│   tool/crawlers/chip_data_scraper.py (融資融券爬蟲)       │
│   tool/crawlers/quarterly_scraper.py (MOPS 季報爬蟲)      │
│   tool/update_monthly_revenue.py (月營收爬蟲)             │
│   tool/update_financials_mops.py (季度財報更新)            │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   配置層 (Config)                         │
│   config.py (所有常數 + V34/V35 Presets + MODE_CMD_MAP)  │
│   .env (敏感資訊: DB_URL, LINE_TOKEN, LINE_SECRET)       │
│   strategy_settings.json (策略啟用狀態, V3 格式)          │
└─────────────────────────────────────────────────────────┘
```

### 完整目錄結構

```
Stock_Linbotv1/
├── app.py                       # Flask 入口 + Line Bot Webhook (port 1688)
├── config.py                    # 全域常數中心（手續費/稅率/策略門檻/Presets）
├── strategy_settings.json       # 策略啟用狀態 (V3 JSON 格式)
├── init_settings.py             # 資料庫 user_settings 表初始化
│
├── 1_update_database.py         # 每日資料更新（股價+籌碼+月營收+季報）
├── 2_rundaily.py                # 每日選股（計算指標→合併財報→多策略篩選→AI評分）
├── 3_train_model.py             # 多策略 XGBoost 批次訓練
├── 4_run_backtest.py            # 多策略回測引擎（含組合回測）
├── 5_push_to_line.py            # Line 推播（SDK v3 Broadcast）
├── 6_optimize_params.py         # Optuna 參數最佳化
│
├── execution/                   # 自動化批次檔
│   ├── morning_run.bat          # 早晨排程 (08:30)
│   ├── evening_run.bat          # 晚間排程 (19:00)
│   ├── daily_run.bat            # 舊版一鍵自動化
│   └── start_web.bat            # 一鍵啟動 Web 服務
│
├── tool/                        # 核心工具模組
│   ├── db_helper.py             # 資料庫操作（唯一合法 DB 存取點）
│   ├── calc_indicators.py       # 技術指標 + 籌碼指標計算
│   ├── model_utils.py           # XGBoost 模型載入（LRU 快取）
│   ├── strategy_manager.py      # 策略工廠（Singleton + Registry）
│   ├── strategy.py              # V30/V31 向後相容層
│   ├── report_helper.py         # 個股 AI 診斷報告聚合
│   ├── line_message_builder.py  # Line Flex Message 卡片建構器
│   ├── viz_helper.py            # Plotly 視覺化 + 回測摘要
│   ├── news_agent.py            # RSS 新聞 + Gemini 分析（實驗性）
│   ├── update_monthly_revenue.py    # 月營收爬蟲（MOPS 靜態 HTML）
│   ├── update_financials_mops.py    # 季度財報更新（MOPS 備援站）
│   ├── update_history_financials.py # 歷史財報批量更新
│   ├── strategies/              # 策略實作目錄
│   │   ├── __init__.py          # 策略模組匯出
│   │   ├── base.py              # BaseStrategy 抽象基底（含 check_exit_signal）
│   │   ├── v31_hybrid.py        # V31 混合策略（MA+RSI+籌碼+XGBoost）
│   │   ├── v33_low_vol.py       # V33 低波動穩健型
│   │   ├── v34_turbo.py         # V34 雙渦輪飆股型
│   │   ├── v35_innovation.py    # V35 經營效益型（營業利益率驅動）
│   │   ├── v36_chip_momentum.py # V36 籌碼動能型
│   │   ├── v37_mean_reversion.py# V37 均值回歸型
│   │   └── v38_value_dividend.py# V38 高殖利率價值型
│   └── crawlers/                # 爬蟲模組
│       ├── chip_data_scraper.py # 融資融券爬蟲（TWSE/TPEx）
│       └── quarterly_scraper.py # MOPS 季報爬蟲（mopsov 備援站）
│
├── templates/                   # Flask Jinja2 模板
│   ├── base.html                # Layout 基底
│   ├── dashboard.html           # 主儀表板
│   ├── backtest.html            # 回測設定頁
│   ├── backtest_result.html     # 回測結果頁（Plotly 圖表）
│   └── login.html               # 登入頁
│
├── static/                      # CSS / JS 靜態資源
│
├── test/                        # pytest 測試套件
│   ├── conftest.py              # 共用 Fixture（manager, empty_df）
│   ├── test_strategy_factory.py
│   ├── test_v35_refactor_flex.py
│   ├── test_phase2_chip_data.py
│   ├── test_v36_chip_momentum.py
│   └── test_v37_v38_strategies.py
│
├── ML_Data/pkl/                 # XGBoost 模型檔（每策略獨立 .pkl）
├── scripts/diagnose_strategies.py  # 策略條件診斷工具
├── doc/                         # 輔助文件
└── openspec/                    # 開發規範與變更管理
    ├── project.md               # 本文件（專案規格）
    ├── AGENTS.md                # AI Agent 編碼規範
    └── specs/                   # 前端/測試規範
```

## 4. 核心流程串接

### 4.1 Windows 排程 — 早晚雙模式

| 排程 | 時間 | 批次檔 | 內容 |
|------|------|--------|------|
| `Stock_Linbot_Morning` | 每天 08:30 | `execution/morning_run.bat` | 早晨大局觀推播 |
| `Stock_Linbot_Evening` | 每天 19:00 | `execution/evening_run.bat` | 資料更新→選股→晚間推播 |

**早晨模式** (`5_push_to_line.py --time morning`)：
```
news_agent.get_morning_news_summary()
  ├── fetch_anue_news()    → 鉅亨網 RSS (美股/國際政經/台股)
  └── Gemini 濃縮          → 3 個台股影響 Bullet Points
+ 隨機策略精選一股 (daily_recommendations Top 1)
→ Flex Message 推播（早安大局觀卡片）
```

**晚間模式** (`execution/evening_run.bat` = 1→2→5 三步驟)：
```
Step 1: 1_update_database.py
  ├── fetch_twse_data()    → 上市股價 + 三大法人籌碼 (TWSE API)
  ├── fetch_tpex_data()    → 上櫃股價 + 三大法人籌碼 (TPEx API)
  ├── fetch_margin_balance() → 融資融券餘額 (chip_data_scraper.py)
  ├── process_and_save()   → 清洗數值 → upsert_stock_data() 寫入 DB
  ├── run_monthly_revenue_update() → MOPS 月營收爬蟲
  └── run_financial_update()       → MOPS 季度財報爬蟲

Step 2: 2_rundaily.py
  ├── compute_indicators_from_history()  → 載入 150 天歷史 → 計算全部指標
  ├── merge_financial_data() + merge_revenue_data()
  └── 遍歷啟用策略 → filter → AI 評分 → 存入 daily_recommendations

Step 3: 5_push_to_line.py --time evening
  ├── 全策略日報（純文字推播）
  └── 明日關注精選（Flex Message 卡片）
```

### 4.2 Web + Line Bot（app.py）

```
Flask App (port 1688)
├── Web Dashboard:
│   ├── GET  /login     → 登入頁（Flask-Login）
│   ├── GET  /dashboard → 儀表板（需登入）
│   ├── GET  /backtest  → 回測設定頁
│   ├── POST /backtest  → 執行回測 → backtest_result.html（Plotly 圖表）
│   └── API:
│       ├── /api/summary        → 市場摘要 JSON
│       ├── /api/daily-signals  → 當日推薦 JSON
│       └── /api/backtest-result→ 回測結果 JSON
│
└── Line Bot Webhook:
    ├── POST /callback → WebhookHandler 驗簽 → MessageEvent
    └── 訊息處理:
        ├── 4 碼數字 → get_stock_report() → create_stock_flex_message() → Flex 卡片
        ├── "推薦"/"選股" → get_best_stocks_v31_hybrid() → create_recommendation_carousel()
        ├── "切換V3x" → strategy_manager.set_active_strategy()
        ├── "設定停損 x" → update_setting()
        ├── "切換積極/平衡/寬鬆" → V34+V35 Preset 批次更新
        ├── "持股" → get_open_holdings() → create_holdings_flex()
        ├── "回測" → get_backtest_summary_from_db() → create_backtest_summary_flex()
        └── "查看設定" → 顯示當前所有參數
```

### 4.3 回測與訓練

```
3_train_model.py:
  遍歷所有策略 → 載入歷史資料 → 計算特徵 → XGBoost 訓練
  → 輸出: ML_Data/pkl/stock_ai_model_{strategy_name}.pkl

4_run_backtest.py:
  ├── 單策略: python 4_run_backtest.py --v33
  ├── 組合回測: python 4_run_backtest.py --portfolio --strategies v33_low_vol,v35_innovation
  └── 加權組合: python 4_run_backtest.py --portfolio --strategies ... --weights 7,3

  流程: 載入歷史 → 策略篩選 → AI 排名 → 模擬交易 → check_exit_signal() 出場
       → save_backtest_results() 寫入 DB → Plotly 視覺化

6_optimize_params.py:
  Optuna + TPE Sampler → 搜尋最佳策略參數（ROI/Sharpe/MDD）
```

## 5. 策略定義

| 版本 | 名稱 | 核心邏輯 | 風險等級 | 停損/停利/持有天數 |
|------|------|---------|---------|-----------------|
| V31 | 混合型 Hybrid | MA 多頭排列 + RSI 40~70 + 量能放大 + XGBoost 排名 | 中 | 7% / 15% / 10天 |
| V33 | 低波動穩健型 | NATR < 3.5% + 收盤 > MA20 > MA60 + 量比 > 1.0 | 低 | 7% / 15% / 10天 |
| V34 | 雙渦輪飆股型 | revenue_yoy > 18% + 收盤 >= 60日高*0.93 + 量比 > 0.9 | 高 | 7% / 15% / 10天 |
| V35 | 經營效益型 | op_profit_margin > 6% + revenue_yoy > 0 + EPS > 0 | 中高 | 7% / 15% / 10天 |
| V36 | 籌碼動能型 | chip_score >= 55 + 外資連買 >= 3天 + 投信連買 >= 2天 | 中高 | 7% / 15% / 12天 |
| V37 | 均值回歸型 | KD 黃金交叉 + BB 收斂 + RSI 30~55 + 低乖離 | 中 | 5% / 10% / 8天 |
| V38 | 高殖利率價值型 | op_margin > 8% + EPS > 0 + NATR < 4% + STD_20 < 3% | 低中 | 6% / 12% / 15天 |

所有策略均繼承 `BaseStrategy`，統一實作 `check_exit_signal()` 出場邏輯（階梯式移動停損：+10%保本 / +20%鎖15% / +30%鎖25%）。

## 6. 資料庫表結構

| 資料表 | 用途 | 寫入來源 |
|--------|------|---------|
| `daily_market_data` | 每日 OHLCV + 技術指標 + 籌碼 | 1_update_database.py, 2_rundaily.py |
| `monthly_revenue` | 月營收 + YoY | 1_update_database.py (MOPS 爬蟲) |
| `financial_statements` | 季度財報（營收/營業利益/EPS） | 1_update_database.py (MOPS 爬蟲) |
| `daily_recommendations` | AI 選股結果（每策略 Top 10） | 2_rundaily.py |
| `user_settings` | 使用者參數（停損/停利/門檻） | app.py (Line Bot 指令) |
| `user_simulation_trades` | PK 模擬交易紀錄 | app.py |
| `backtest_trades` | 回測交易明細 | 4_run_backtest.py |
| `backtest_equity_curve` | 回測權益曲線 | 4_run_backtest.py |

## 7. 架構限制（強制規範）

1. **資料庫存取**：所有 DB 操作必須透過 `tool/db_helper.py`，禁止在 `app.py` 或策略類別中直接使用 SQL。
2. **常數管理**：手續費（0.001425）、交易稅（0.003）、滑價等一律定義於 `config.py`。
3. **策略擴充**：新策略必須繼承 `BaseStrategy`，並在 `tool/strategies/__init__.py` + `strategy_manager.py` STRATEGY_REGISTRY 中註冊。
4. **前端開發**：不使用 npm/webpack，僅使用 CDN（Tailwind / Alpine.js / Chart.js）；視覺規範參照 `openspec/specs/frontend-design.md`。
5. **提案流程**：涉及多個模組的變更需先建立 `openspec/changes/<id>/proposal.md`，取得確認後方可實作。
6. **安全規範**：敏感資訊（DB_URL, LINE_TOKEN）一律走 `.env` 環境變數；SQL 一律參數化。

## 8. 啟動方式

### 8.1 前置準備

```powershell
# 建立虛擬環境 + 安裝依賴
python -m venv myenv
.\myenv\Scripts\Activate.ps1
pip install -r requirements.txt

# 設定環境變數（複製 .env.example 為 .env）
# DB_URL=mysql+pymysql://user:password@localhost:3306/stock_ai_db
# LINE_TOKEN=你的_Channel_Access_Token
# LINE_SECRET=你的_Channel_Secret
# ADMIN_PASSWORD=Web登入密碼

# 初始化資料庫
python init_settings.py
```

### 8.2 日常啟動

```powershell
# 方式 A: 一鍵自動化（推薦，含更新+選股+推播）
execution\daily_run.bat

# 方式 B: 手動分步
python 1_update_database.py   # Step 1: 爬蟲更新資料
python 2_rundaily.py          # Step 2: 選股
python 5_push_to_line.py      # Step 3: Line 推播

# 啟動 Web + Line Bot
python app.py                 # port 1688
# 或
execution\start_web.bat       # 背景啟動
```

## 9. 測試方式

### 9.1 爬蟲測試

```powershell
# 語法檢查
python -m py_compile 1_update_database.py

# 冒煙測試（會實際連線 TWSE/TPEx/MOPS，需確保網路通暢）
python 1_update_database.py
# 觀察: 步驟 1/3 股價 → 步驟 2/3 月營收 → 步驟 3/3 季報
# 正常: "成功寫入 xxxx 筆資料"
# 假日: "假日或無資料"
```

### 9.2 Web Dashboard 測試

```powershell
python app.py
# 瀏覽器開啟:
#   http://localhost:1688/login      → 測試登入（密碼見 ADMIN_PASSWORD）
#   http://localhost:1688/dashboard  → 主儀表板
#   http://localhost:1688/backtest   → 回測頁面
# API 端點:
#   http://localhost:1688/api/summary
#   http://localhost:1688/api/daily-signals
```

### 9.3 Line Bot 測試

```powershell
# 前提: 已設定 LINE_TOKEN + LINE_SECRET + Webhook URL（需 HTTPS）
# 本地開發可用 ngrok:
#   ngrok http 1688
#   將 https://xxxx.ngrok.io/callback 設為 Line Webhook URL

python app.py

# Line 測試指令:
#   "2330"     → 個股診斷 Flex 卡片
#   "推薦"     → AI 選股推薦
#   "查看策略"  → 顯示當前策略
#   "切換V33"   → 切換策略
#   "設定停損 5" → 調整停損
#   "持股"      → 查看模擬持倉
```

### 9.4 自動化測試

```powershell
# 全量測試（推薦）
python -m pytest test/ -v --tb=short

# 分模組測試
python -m pytest test/test_strategy_factory.py -v      # 策略載入
python -m pytest test/test_v35_refactor_flex.py -v     # Flex + 出場邏輯
python -m pytest test/test_phase2_chip_data.py -v      # 籌碼指標
python -m pytest test/test_v36_chip_momentum.py -v     # V36 策略
python -m pytest test/test_v37_v38_strategies.py -v    # V37+V38 策略

# 回測冒煙測試
python 4_run_backtest.py --v31
```

## 10. 當前狀態（2026 年 3 月）

- **多策略並行**：V31~V38 共 7 種策略，透過 `strategy_settings.json` 切換或多選並行。
- **Line Bot Flex Message**：輸入股票代號可獲得三維度（技術+基本面+AI）診斷卡片。
- **策略出場邏輯**：統一由 `BaseStrategy.check_exit_signal()` 管理，階梯式移動停損。
- **組合回測**：支援多策略組合 + 加權分配，含 Plotly 互動式圖表。
- **每日自動化**：`execution/` 下的 BAT 排程（morning 08:30 / evening 19:00）全自動運行。
- **安全強化**：環境變數隔離 + Web 登入驗證 + SQL 參數化 + DB 連線重試。
- **當前啟用策略**：V38 高殖利率價值型（可於 strategy_settings.json 或 Line Bot 切換）。
