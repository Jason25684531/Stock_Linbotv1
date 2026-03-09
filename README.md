# Stock AI Line Bot V38

> 🧠 **Multi-Model Pipeline** | 每策略獨立 AI 模型，動態載入推論
> 🔥 **7 Strategy Factory** | V31/V33/V34/V35/V36/V37/V38 策略工廠 + BaseStrategy 繼承體系
> 📊 **Chip Data Infrastructure** | 融資融券 + 自營商 + `chip_score` 籌碼綜合分數
> 💼 **V35 經營效益策略** | 專注營業利益率高效益成長股
> 🔄 **V37 均值回歸** | KD 黃金交叉 + 布林收斂 + 低基期反轉
> 💰 **V38 高殖利率** | 營業利益率 + EPS 正成長 + 低波動價值股
> 📲 **Line Bot Flex Message** | 輸入股票代號即取得 AI 健康診斷卡片
> ⚔️ **PK System 人機對決** | 模擬交易與 AI 績效比較
> 🔐 **Security Hardening** | 環境變數隔離 + Web 登入驗證 + SQL 注入修復 + DB 重試
> 📊 **Backtesting Engine** | 組合回測 + 互動式圖表 + MDD 修復
> 📅 **最後更新**: 2026-03-09
> ✅ **系統狀態**: 穩定運行（多策略並行 + 每日自動化 + Flex Message 診斷卡片）

---

## 📊 專案簡介

整合 AI 機器學習與技術分析的台股選股系統，透過 Line Bot 提供即時選股推薦與個股診斷，並支援多策略組合回測與視覺化分析。

### 🎯 核心功能

| 功能 | 說明 | 命令/路徑 |
|------|------|---------|
| 🎯 多策略並行 | 同時啟用多個策略 (V31/V33/V34/V35/V36/V37/V38)，分散風險 | Web Dashboard 核取方塊 |
| 📊 組合回測 | 多策略投資組合回測 + Plotly 視覺化 | `/backtest` |
| 🔐 登入驗證 | Web Dashboard 需密碼登入 | `/login` |
| 🔥 V31 混合策略 | V30 篩選 + XGBoost 排名 | 輸入「推薦」 |
| 🚀 V30 純技術策略 | 均線突破 + 量能確認 + 大盤熔斷 | 輸入「V30」 |
| 🎫 個股 AI 診斷 (Flex) | 三維度健康診斷卡片（技術面+基本面+AI分數）| 輸入 4 碼股票代號 |
| 📊 Web Dashboard | 視覺化回測績效與即時選股 | `http://localhost:1688` |

### 🧭 當前策略門檻（2026-03）

| 策略 | 目前核心條件 | 說明 |
|------|-------------|------|
| V31 混合 | V30 技術篩選 + XGBoost AI 排名 | 均衡型基準策略 |
| V33 低波動 | `NATR < 3.5%` + `收盤 > MA20 > MA60` + `量比 > 1.0` | 穩健型，降低停損噪音 |
| V34 雙渦輪 | `revenue_yoy > 18%` + `收盤 >= 60日高 * 0.93` + `volume_ratio > 0.9` | 積極型高成長飆股 |
| V35 經營效益 | `op_profit_margin > 6%` + `revenue_yoy > 0` + `EPS > 0` | 成長型，專注營業利益率 |
| V36 籌碼動能 | `chip_score ≥ 55` + `外資連買 ≥ 3 天` + `投信連買 ≥ 2 天` | 追蹤型，跟隨法人佈局 |
| V37 均值回歸 | KD 黃金交叉 + BB 收斂 + `RSI 25~45` | 反轉型，低基期反彈 |
| V38 高殖利率 | `op_margin > 8%` + `EPS > 0.5` + `NATR < 3%` + `STD_20 < 2.5` | 價值型，穩配息低波動 |

---

## 🏗️ 系統架構 (V38)

**設計原則**：DRY + 單一職責 + 統一介面 + 安全優先 + 資料驅動

```
┌─────────────────────────────────────────────────────────┐
│                  🌐 應用層 (Application)                │
│   app.py (Flask + Line Bot Webhook, port 1688)          │
│   daily_run.bat (自動化: 更新→選股→推播)                  │
│   1~6_*.py (資料更新/選股/訓練/回測/推播/最佳化)          │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│         📊 呈現層 (Presentation)                        │
│   templates/ (dashboard, backtest, login)                │
│   tool/line_message_builder.py (Flex Message 卡片)       │
│   tool/viz_helper.py (Plotly 互動式圖表)                 │
│   tool/report_helper.py (個股 AI 診斷報告)               │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                 📊 策略層 (Multi-Strategy)              │
│   tool/strategy_manager.py (Singleton 工廠, 7 策略註冊)  │
│   tool/strategies/base.py (BaseStrategy + check_exit)    │
│   tool/strategies/v31~v38 (各策略實作)                    │
│   tool/strategy.py (V30/V31 向後相容層)                   │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   📈 指標層 (Indicators)                 │
│   tool/calc_indicators.py                                │
│   (MA, RSI, MACD, KD, BB, ATR, NATR, chip_score)        │
│   tool/model_utils.py (XGBoost 模型載入, LRU 快取)       │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   🗄️ 資料層 (Data)                       │
│   tool/db_helper.py (唯一 DB 存取入口, Singleton 連線池) │
│   tool/crawlers/ (融資融券 + MOPS 季報爬蟲)              │
│   tool/update_monthly_revenue.py (月營收爬蟲)             │
│   tool/update_financials_mops.py (季度財報更新)           │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   ⚙️ 配置層 (Config)                     │
│   config.py (常數 + V34/V35 Presets + MODE_CMD_MAP)      │
│   .env (敏感資訊: DB_URL, LINE_TOKEN, LINE_SECRET)       │
│   strategy_settings.json (策略啟用狀態, V3 格式)          │
└─────────────────────────────────────────────────────────┘
```

### 🔑 關鍵設計決策

| 原則 | 實施方式 | 效益 |
|------|---------|------|
| **策略解耦** | `BaseStrategy.check_exit_signal()` 統一出場邏輯 | 回測引擎只做調度，不含策略判斷 |
| **組合回測** | `PortfolioBacktestEngine` 支援多策略，各策略載入專屬 AI 模型 | 驗證策略組合績效 |
| **視覺化** | Plotly 互動式圖表 | 直觀展示權益曲線與回撤 |
| **多策略並行** | `StrategyManager` 支援列表形式，7 策略註冊 | 同時運行 V33+V34+V36…，分散風險 |
| **安全優先** | 敏感資訊隔離至 `.env`，Web 需登入，SQL 參數化 | 防止 SQL 注入與資料外洩 |
| **統一入口** | 所有 DB 操作經 `tool.db_helper`（含 safe_float/safe_int/get_open_holdings） | 防 SQL Injection、易測試 |
| **Flex Message** | `line_message_builder.py` 建構互動卡片 | 取代純文字，視覺化診斷 |
| **資料驅動** | 模式切換 / Preset 參數集中於 `config.py`（MODE_CMD_MAP 等） | 新增模式只改 config，不改 app.py |
| **Fixture 共用** | `test/conftest.py` 統一 manager + empty_df | 測試 DRY，新策略零 boilerplate |

---

## 🛡️ V33+ 策略強化特性

### 風險控制機制

| 機制 | 說明 | 設定 |
|------|------|------|
| 🔥 大盤熔斷 | 收盤 < MA60 時禁止買入 | `USE_MARKET_FILTER = True` |
| 📈 趨勢濾網 | 個股收盤 > MA60 | `USE_TREND_FILTER = True` |
| 🛡️ ATR 動態停損 | 波動大則寬，波動小則窄 | `USE_ATR_STOP = True` |
| 🧪 測試模式 | 強制多頭市場（開發測試用） | `$env:FORCE_BULL_MARKET="true"` |

### ATR 停損計算

```python
# 停損價格 = 收盤價 - (ATR × 乘數)
stop_loss = close - (atr * Config.ATR_MULTIPLIER)  # 預設乘數 2.0
```

### 測試模式使用

```powershell
# 啟用測試模式（強制多頭市場）
$env:FORCE_BULL_MARKET="true"; python 2_rundaily.py

# 關閉測試模式（正式環境）
Remove-Item Env:FORCE_BULL_MARKET; python 2_rundaily.py
```

---

## 🚀 快速開始

### 環境需求

- Python 3.8+
- MySQL 8.0+ (或 Docker)
- 建議 8GB RAM

### 安裝步驟

```powershell
# 1. Clone 專案
git clone <your-repo-url> Stock_Linbotv1
cd Stock_Linbotv1

# 2. 建立虛擬環境
python -m venv myenv
.\myenv\Scripts\activate

# 3. 安裝套件
pip install -r requirements.txt

# 4. 設定環境變數
# 複製 .env.example 為 .env，填入實際值
copy .env.example .env

# 必要設定：
# - DB_URL=mysql+pymysql://user:password@localhost:3306/stock_ai_db
# - LINE_TOKEN=你的_Line_Channel_Access_Token
# - LINE_SECRET=你的_Line_Channel_Secret
# - ADMIN_PASSWORD=Web_Dashboard_密碼
# - GEMINI_KEY=你的_Google_Gemini_API_Key（早晨新聞摘要所需）

# 5. 初始化資料庫
python init_settings.py
```

### 每日自動化排程（Windows 工作排程器）

系統已註冊兩個 Windows 排程，全自動運行：

| 排程 | 時間 | 批次檔 | 內容 |
|------|------|--------|------|
| `Stock_Linbot_Morning` | 每天 08:30 | `morning_run.bat` | 早晨大局觀（新聞摘要 + 精選一股） |
| `Stock_Linbot_Evening` | 每天 19:00 | `evening_run.bat` | 資料更新 → 選股 → 晚間推播 |

### 手動執行推播

```powershell
# 早晨大局觀（Gemini 新聞摘要 + 隨機策略精選 Flex）
python 5_push_to_line.py --time morning

# 晚間選股策劃（全策略推薦 + 明日關注 Flex）
python 5_push_to_line.py --time evening

# 完整晚間流程（資料更新 → 選股 → 推播）
execution\evening_run.bat
```

### 手動分步執行

```powershell
python 1_update_database.py   # 爬取股價+籌碼+月營收+季報
python 2_rundaily.py          # 計算指標+選股+AI評分
python 5_push_to_line.py      # Line 推播（預設 evening 模式）
```

### 啟動與關閉（Windows / 虛擬環境）

```powershell
# 啟動虛擬環境
.\myenv\Scripts\Activate.ps1

# 啟動 Web + Line Bot（port 1688）
python app.py

# 關閉服務
# 在執行中的終端按 Ctrl + C

# 退出虛擬環境
deactivate
```

### 回測與視覺化 (Phase 5 新功能)

```powershell
# 單一策略回測
python 4_run_backtest.py --v31

# 多策略組合回測
python 4_run_backtest.py --portfolio --strategies v33_low_vol,v35_innovation

# 多策略「權重」組合回測（新支援）
# 權重可為任意正數，系統會自動正規化；下例 = 70% / 30%
python 4_run_backtest.py --portfolio --strategies v33_low_vol,v35_innovation --weights 7,3

# Web 回測（推薦）
python app.py
# 瀏覽器開啟 http://localhost:1688/backtest
# 1. 選擇策略組合（可多選）
# 2. 設定回測期間（預設 1 年）
# 3. 查看互動式圖表與績效指標
```

---

## 📊 Phase 5: Backtesting & Visualization

### 功能特色

**1. 多策略組合回測**
- ✅ 支援同時回測 2+ 策略
- ✅ 資金平均分配
- ✅ 每日資產曲線追蹤
- ✅ 各策略績效比較

**2. Plotly 互動式圖表**
- 📈 權益曲線圖（Portfolio vs Benchmark）
- 📉 回撤分析圖（Underwater Plot）
- 📅 月度報酬熱力圖
- 🎯 績效指標卡片

**3. 績效指標計算**
- **CAGR** (年化複合成長率)
- **Sharpe Ratio** (夏普比率)
- **Max Drawdown** (最大回撤)
- **Win Rate** (勝率)
- **Profit Factor** (盈虧比)

```powershell
# 執行回測
python 4_run_backtest.py

# 重新訓練模型（為每個策略生成獨立 AI 模型）
python 3_train_model.py
# 輸出: stock_ai_model_v33_low_vol.pkl, stock_ai_model_v34_turbo.pkl, ...

# 參數最佳化 (可選)
python 6_optimize_params.py --objective roi --n-trials 100
```

## ✅ 全功能測試方式（建議順序）

```powershell
# 0) 進入虛擬環境
.\myenv\Scripts\Activate.ps1

# 1) 語法檢查（核心入口）
python -m py_compile app.py
python -m py_compile 2_rundaily.py
python -m py_compile 4_run_backtest.py

# 2) 全量單元 / 整合測試（一鍵執行）
python -m pytest test/ -v --tb=short
# 預期結果: 117 passed

# 3) 依模組分別測試（選擇性執行）
python -m pytest test/test_strategy_factory.py -v      # 策略載入 & 篩選 (3 tests)
python -m pytest test/test_v35_refactor_flex.py -v     # Flex Message + 出場邏輯 (13 tests)
python -m pytest test/test_phase2_chip_data.py -v      # 籌碼指標計算 (16 tests)
python -m pytest test/test_v36_chip_momentum.py -v     # V36 籌碼動能策略 (29 tests)
python -m pytest test/test_v37_v38_strategies.py -v    # V37 均值回歸 + V38 高殖利率 (56 tests)

# 4) 回測冒煙測試
python 4_run_backtest.py --v31

# 5) 日常流程冒煙測試
python 2_rundaily.py

# 6) 啟動 Web 並手動驗證 API
python app.py
# 瀏覽器: http://localhost:1688/dashboard
# API 端點: /api/summary, /api/daily-signals, /api/backtest-result
```

> 若僅需快速回歸，至少執行步驟 1 + 2 + 4。

---

## 📁 目錄結構

```
Stock_Linbotv1/
├── 📊 每日流程腳本 (依執行順序編號)
│   ├── 1_update_database.py     # 爬取股價+籌碼+月營收+季報
│   ├── 2_rundaily.py            # 計算指標+多策略選股+AI評分
│   ├── 3_train_model.py         # 多策略 XGBoost 批次訓練
│   ├── 4_run_backtest.py        # 多策略回測引擎（含組合回測）
│   ├── 5_push_to_line.py        # Line 推播 (SDK v3 Broadcast)
│   └── 6_optimize_params.py     # Optuna 參數最佳化
│
├── 🚀 execution/ — 自動化腳本 + Windows 排程
│   ├── morning_run.bat          # 早晨排程 (08:30): 新聞摘要+精選推播
│   ├── evening_run.bat          # 晚間排程 (19:00): 更新→選股→推播
│   ├── daily_run.bat            # 一鍵自動化 (1→2→5 三步驟，舊版相容)
│   └── start_web.bat            # 一鍵啟動 Web 服務
│
├── 🌐 使用者介面
│   ├── app.py                   # Flask + Line Bot (port 1688)
│   └── templates/               # Web Dashboard (Jinja2)
│       ├── base.html            # Layout 基底
│       ├── dashboard.html       # 主儀表板
│       ├── backtest.html        # 回測設定頁
│       ├── backtest_result.html # 回測結果（Plotly 圖表）
│       └── login.html           # 登入頁
│
├── ⚙️ 核心模組 (tool/) — 統一共用函數，禁止 raw SQL
│   ├── strategy_manager.py      # 策略工廠 (Singleton + Registry, 7 策略)
│   ├── strategies/              # 策略實作目錄
│   │   ├── base.py              # BaseStrategy 抽象基底 (含 check_exit_signal)
│   │   ├── v31_hybrid.py        # V31 混合策略 (V30+XGBoost)
│   │   ├── v33_low_vol.py       # V33 低波動策略
│   │   ├── v34_turbo.py         # V34 高成長策略
│   │   ├── v35_innovation.py    # V35 經營效益策略
│   │   ├── v36_chip_momentum.py # V36 籌碼動能策略
│   │   ├── v37_mean_reversion.py# V37 均值回歸策略
│   │   └── v38_value_dividend.py# V38 高殖利率價值策略
│   ├── strategy.py              # V30/V31 向後相容層 (格式化函式)
│   ├── line_message_builder.py  # Line Flex Message 卡片建構器
│   ├── report_helper.py         # 個股 AI 診斷報告聚合
│   ├── calc_indicators.py       # 技術指標 + 籌碼指標 (唯一來源)
│   ├── viz_helper.py            # Plotly 視覺化 + 回測摘要
│   ├── db_helper.py             # 資料庫操作 (唯一入口)
│   ├── model_utils.py           # XGBoost 模型載入工具 (LRU 快取)
│   ├── update_monthly_revenue.py    # 月營收爬蟲 (MOPS 靜態 HTML)
│   ├── update_financials_mops.py    # 季度財報更新 (MOPS 備援站)
│   ├── update_history_financials.py # 歷史財報批量更新
│   ├── news_agent.py            # RSS 新聞 + Gemini 分析 (實驗性)
│   └── crawlers/                # 爬蟲模組
│       ├── chip_data_scraper.py # 融資融券爬蟲 (TWSE/TPEx)
│       └── quarterly_scraper.py # MOPS 季報爬蟲 (mopsov 備援站)
│
├── 🧪 測試 (test/) — pytest + conftest 共用 Fixture
│   ├── conftest.py              # 共用 Fixture (manager, empty_df)
│   ├── test_strategy_factory.py # 策略載入 & 篩選
│   ├── test_v35_refactor_flex.py# Flex + 出場邏輯 + 向後相容
│   ├── test_phase2_chip_data.py # 籌碼指標
│   ├── test_v36_chip_momentum.py# V36 策略
│   └── test_v37_v38_strategies.py# V37+V38 策略
│
├── 📦 數據與模型
│   └── ML_Data/pkl/             # XGBoost 模型 (每策略獨立 .pkl)
│
├── 🔧 工具腳本
│   └── scripts/diagnose_strategies.py  # 策略條件診斷
│
├── 📄 設定檔
│   ├── config.py                # 統一設定中心 (常數+Presets+MODE_CMD_MAP)
│   ├── strategy_settings.json   # 策略啟用狀態 (V3 JSON 格式)
│   ├── init_settings.py         # 資料庫初始化腳本
│   └── .env                     # 敏感資訊 (DB_URL, LINE_TOKEN 等)
│
└── 📝 文檔
    ├── README.md                # 本文件
    ├── doc/                     # 輔助文件
    └── openspec/                # 開發規範 (project.md, AGENTS.md)
```

### 核心流程串接

**每日自動化流程 (`daily_run.bat`)**：
```
1_update_database.py          2_rundaily.py                    5_push_to_line.py
  ├ TWSE API → 上市股價       ├ 載入 150 天歷史資料              ├ 讀取 daily_recommendations
  ├ TPEx API → 上櫃股價       ├ compute_indicators_from_history  ├ 組合推播訊息
  ├ chip_data_scraper          │  (MA/RSI/MACD/KD/BB/ATR/NATR  └ Line SDK v3 Broadcast
  │  → 融資融券餘額             │   chip_score/consec_days)
  ├ MOPS → 月營收 YoY         ├ merge_financial_data (季報)
  └ MOPS → 季度財報            ├ merge_revenue_data (月營收)
     ↓ 寫入 DB                 └ 遍歷策略:
                                  ├ filter_candidates() 硬篩選
                                  ├ load_model() AI 模型
                                  ├ predict_proba() 評分
                                  └ 存入 daily_recommendations
```

**Web + Line Bot (`app.py`, port 1688)**：
```
Flask App
├ Web: /login → /dashboard → /backtest → /backtest_result (Plotly)
├ API: /api/summary, /api/daily-signals, /api/backtest-result
└ Line Bot: POST /callback
   ├ 4碼數字 → report_helper → Flex 卡片
   ├ "推薦" → strategy.get_best_stocks → Carousel
   ├ "切換V3x" → strategy_manager.set_active_strategy
   └ "設定停損 x" → db_helper.update_setting
```

### 模組依賴關係

```
應用層 (app.py, 1-6_*.py, daily_run.bat)
    ↓
呈現層 (templates/, line_message_builder.py, viz_helper.py, report_helper.py)
    ↓
策略層 (strategy_manager.py → strategies/base.py → V31~V38)
    ↓
指標層 (calc_indicators.py, model_utils.py)
    ↓
資料層 (db_helper.py, crawlers/, update_*.py)
    ↓
配置層 (config.py + .env + strategy_settings.json)
```

**設計原則**:
- 所有資料庫操作必須通過 `tool/db_helper.py`，禁止 raw SQL
- 所有技術指標計算必須通過 `tool/calc_indicators.py`（唯一真理來源）
- 所有策略繼承 `BaseStrategy`，共用日期提取與大盤熔斷邏輯
- 所有常數定義必須在 `config.py`
- Line Bot 統一使用 SDK v3（`linebot.v3.messaging`）
- 敏感資訊一律走 `.env` 環境變數
- 測試 Fixture 統一由 `test/conftest.py` 提供

---

## 📱 Line Bot 指令

### 🎯 選股功能

| 指令 | 功能 | 說明 |
|------|------|------|
| `推薦` / `選股` / `AI` | 當前策略選股 | 根據活躍策略推薦前 5 名（支援 Flex 卡片） |
| `V30` / `策略` | 純技術選股 | 均線突破 + 量能確認 |
| `2330`（4 碼數字） | 個股 AI 診斷 | Flex 卡片：技術面 + 基本面 + AI 分數 |
| `查詢 台積電` | 名稱搜股 | 模糊搜尋股票名稱 |
| `持股` / `AI持股` / `庫存` | 查看持股 | AI 模擬交易持倉明細 |
| `回測` / `績效` / `backtest` | 回測摘要 | 最新回測績效報告 |
| `dashboard` / `儀表板` | 儀表板連結 | 取得 Web Dashboard URL |

### 🔄 策略切換

| 指令 | 對應策略 | 風格 |
|------|---------|------|
| `切換V30` / `切V30` | V31 混合策略 | 均衡型 |
| `切換V33` / `低波動` | V33 低波動 | 穩健型 |
| `切換V34` / `飆股` / `渦輪` | V34 雙渦輪 | 積極型 |
| `切換V35` / `經營效益` / `創新` | V35 經營效益 | 成長型 |
| `切換V36` / `籌碼` / `法人` | V36 籌碼動能 | 追蹤型 |
| `切換V37` / `均值回歸` / `反轉` | V37 均值回歸 | 反轉型 |
| `切換V38` / `高殖利` / `定存股` | V38 高殖利率 | 價值型 |
| `查看策略` / `目前策略` | 顯示策略詳情 | — |

### ⚙️ 參數調整

| 指令 | 功能 | 範例 |
|------|------|------|
| `設定停損 5` | V30 停損 5% | 範圍 1%-20% |
| `設定停利 20` | V30 停利 20% | `設定停利 0` = 不停利 |
| `查看設定` | 查看所有參數 | 顯示 V30/V34/V35 設定值 |
| `切換積極` / `切換平衡` / `切換寬鬆` | 全域模式切換 | 同時調整 V34+V35 參數 |
| `V34積極` / `V34平衡` / `V34寬鬆` | V34 個別模式 | 僅調整 V34 參數 |
| `V35積極` / `V35平衡` / `V35寬鬆` | V35 個別模式 | 僅調整 V35 參數 |
| `設定V34 18 0.93 0.9` | V34 嚴格參數 | YoY% / 突破比 / 量比 |
| `設定V34放寬 10 0.90 0.7` | V34 放寬參數 | 放寬版三參數 |
| `設定V35 6 0 0.8` | V35 嚴格參數 | 利益率% / YoY / 量比 |
| `設定V35放寬 4 -5 0.6` | V35 放寬參數 | 放寬版三參數 |

---

## 📋 相關文件

- **[UpdateList.md](UpdateList.md)** - 版本更新記錄
- **[config.py](config.py)** - 參數設定說明
- **[openspec/project.md](openspec/project.md)** - 專案規格

---

## ⚠️ 風險警告

本系統僅供學習研究使用，不構成投資建議。投資有風險，請自行評估。

---

## 📞 支援

如有問題，請提交 Issue 或參考 [UpdateList.md](UpdateList.md) 中的常見問題排查。

---

**版本**: V38 (2026-03-09)
**授權**: MIT License
**最新變更**:
- ✅ **早晚雙模式推播**：`--time morning` 早晨大局觀 / `--time evening` 晚間選股策劃
- ✅ **Gemini 新聞摘要**：鉅亨網 RSS → Gemini 濃縮為 3 個台股影響 Bullet Points
- ✅ **Windows 排程**：`Stock_Linbot_Morning` (08:30) + `Stock_Linbot_Evening` (19:00)
- ✅ **Flex Message 推播**：早晨精選一股卡片 + 晚間明日關注卡片
- ✅ 多策略並行：V31~V38 共 7 種策略
- ✅ 組合回測 + Plotly 互動式視覺化
- ✅ Line Bot SDK v3 + 安全強化
