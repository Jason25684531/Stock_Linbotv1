# Stock AI Line Bot V35

> 🧠 **Multi-Model Pipeline** | 每策略獨立 AI 模型，動態載入推論  
> 🔥 **Strategy Decoupling** | 出場邏輯委派 `check_exit_signal` + 參數化 SQL  
> 💼 **V35 經營效益策略** | 專注營業利益率高效益成長股  
> 📲 **Line Bot 診斷** | 輸入股票代號即取得 AI 健康診斷書  
> ⚔️ **PK System 人機對決** | 模擬交易與AI 績效比較  
> 🔐 **Security Hardening** | 環境變數隔離 + Web 登入驗證 + SQL 注入修復  
> 📊 **Backtesting Engine** | 組合回測 + 互動式圖表 + MDD 修復  
> 📅 **最後更新**: 2026-02-13  
> ✅ **系統狀態**: 穩定運行（策略解耦 + 重複邏輯整併 + 文件同步）

---

## 📊 專案簡介

整合 AI 機器學習與技術分析的台股選股系統，透過 Line Bot 提供即時選股推薦與個股診斷，並支援多策略組合回測與視覺化分析。

### 🎯 核心功能

| 功能 | 說明 | 命令/路徑 |
|------|------|---------|
| 🎯 多策略並行 | 同時啟用多個策略 (V31/V33/V34/V35)，分散風險 | Web Dashboard 核取方塊 |
| 📊 組合回測 | 多策略投資組合回測 + Plotly 視覺化 | `/backtest` |
| 🔐 登入驗證 | Web Dashboard 需密碼登入 | `/login` |
| 🔥 V31 混合策略 | V30 篩選 + XGBoost 排名 | 輸入「推薦」 |
| 🚀 V30 純技術策略 | 均線突破 + 量能確認 + 大盤熔斷 | 輸入「V30」 |
| 🎫 個股 AI 診斷 | 三維度健康診斷（技術面+基本面+AI分數） | 輸入 4 碼股票代號 |
| 📊 Web Dashboard | 視覺化回測績效與即時選股 | `http://localhost:8866` |

### 🧭 當前策略門檻（2026-02）

| 策略 | 目前核心條件 | 說明 |
|------|-------------|------|
| V33 低波動 | `NATR < 3.5%` + `收盤 > MA20 > MA60` + `量比 > 1.0` | 第二階段微調，降低停損噪音 |
| V34 雙渦輪 | `revenue_yoy > 18%` + `收盤 >= 60日高 * 0.93` + `volume_ratio > 0.9` | 修正單日資料卡點後可正常出手 |
| V35 經營效益 | `op_profit_margin > 6%` + `revenue_yoy > 0` + `EPS > 0` | 已由 10% 放寬至 6% |

---

## 🏗️ 系統架構 (V35 Phase 5)

**設計原則**：DRY + 單一職責 + 統一介面 + 安全優先

```
┌─────────────────────────────────────────────────────────┐
│                  🌐 應用層 (Application)                │
│   🔐 Flask Login │ Line Bot │ Web Dashboard             │
│   1-7_*.py (自動化腳本)                                   │
└────────────┬────────────────────────────────────────────┘
             │ 統一使用共用函數，無重複代碼
┌────────────▼────────────────────────────────────────────┐
│         📊 回測層 (Backtesting & Visualization)         │
│   4_run_backtest.py (策略委派出場 → check_exit_signal)   │
│   tool/viz_helper.py (Plotly 視覺化)                     │
└────────────┬────────────────────────────────────────────┘
             │ 回測依賴策略邏輯
┌────────────▼────────────────────────────────────────────┐
│                 📊 策略層 (Multi-Strategy)              │
│   tool/strategy_manager.py (策略工廠，支援多策略並行)     │
│   tool/strategies/base.py (BaseStrategy + check_exit)    │
│   tool/strategies/ (V31, V33, V34, V35 繼承 base)       │
│   tool/strategy.py (V30/V31 共用邏輯)                    │
│   tool/report_helper.py (個股 AI 診斷報告)               │
└────────────┬────────────────────────────────────────────┘
             │ 策略依賴技術指標與資料查詢
┌────────────▼────────────────────────────────────────────┐
│                   📈 分析層 (Indicators)                 │
│   tool/calc_indicators.py                                │
│   (MA, RSI, MACD, KD, BB, ATR, 比例特徵)                 │
└────────────┬────────────────────────────────────────────┘
             │ 指標計算需要資料支持
┌────────────▼────────────────────────────────────────────┐
│                   🗄️ 資料層 (Data)                       │
│   tool/db_helper.py (統一資料庫入口)                      │
│   - get_db_engine(): 資料庫連接                          │
│   - get_stock_data(): 股票資料查詢                       │
│   - get_market_trend(): 市場趨勢判斷                     │
│   - get/update_setting(): 參數管理                       │
│   - create_user_simulation_trade(): PK 模擬交易寫入       │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   ⚙️ 配置層 (Config)                     │
│   config.py + .env (環境變數隔離)                         │
│   MySQL (Docker) + XGBoost Model (.pkl)                  │
└─────────────────────────────────────────────────────────┘
```

### 🔑 關鍵設計決策 (V35 Phase 5)

| 原則 | 實施方式 | 效益 |
|------|---------|------|
| **策略解耦** | `BaseStrategy.check_exit_signal()` 統一出場邏輯 | 回測引擎只做調度，不含策略判斷 |
| **組合回測** | `PortfolioBacktestEngine` 支援多策略，各策略載入專屬 AI 模型 | 驗證策略組合績效 |
| **視覺化** | Plotly 互動式圖表 | 直觀展示權益曲線與回撤 |
| **多策略並行** | `StrategyManager` 支援列表形式 | 同時運行 V33+V34，分散風險 |
| **安全優先** | 敏感資訊隔離至 `.env`，Web 需登入，SQL 參數化 | 防止 SQL 注入與資料外洩 |
| **統一入口** | 所有 DB 操作經 `tool.db_helper` | 防 SQL Injection、易測試 |
| **無重複代碼** | 共用函數取代本地實作 | 減少 450+ 行代碼 |

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

# 5. 初始化資料庫
python init_settings.py
```

### 每日更新流程

```powershell
# 一鍵整合腳本（推薦）
python 2_rundaily.py

# 或手動分步執行
python 1_update_database.py                           # 爬取股價
python -c "from tool.calc_indicators import fix_database_indicators; fix_database_indicators()"  # 計算指標
python 5_push_to_line.py                              # Line 推播
```

### 啟動與關閉（Windows / 虛擬環境）

```powershell
# 啟動虛擬環境
.\myenv\Scripts\Activate.ps1

# 啟動 Web + Line Bot（port 8866）
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

# Web 回測（推薦）
python app.py
# 瀏覽器開啟 http://localhost:8866/backtest
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

# 2) 單元/整合測試（核心）
python -m pytest test/test_strategy_factory.py -v
python -m pytest test/test_phase3_integration.py -v

# 3) 回測冒煙測試
python 4_run_backtest.py --v31

# 4) 日常流程冒煙測試
python 2_rundaily.py

# 5) 啟動 Web 並手動驗證 API
python app.py
# 瀏覽器: http://localhost:8866/dashboard
# API: /api/summary, /api/live_signals, /api/daily-signals
```

> 若僅需快速回歸，至少執行步驟 1 + 2 + 3。

---

## 📁 目錄結構

```
Stock_Linbotv1/
├── 📊 每日流程腳本 (依執行順序編號)
│   ├── 1_update_database.py     # 爬取股價 + 籌碼
│   ├── 2_rundaily.py            # 整合腳本 (一鍵執行)
│   ├── 3_train_model.py         # 多策略 XGBoost 批次訓練
│   ├── 4_run_backtest.py        # 統一回測引擎
│   ├── 5_push_to_line.py        # Line 推播
│   └── 6_optimize_params.py     # Optuna 參數優化
│
├── 🌐 使用者介面
│   ├── app.py                   # Flask + Line Bot
│   ├── debug_local.py           # 本地互動測試
│   └── templates/               # Web Dashboard
│
├── ⚙️ 核心模組 (tool/) - 統一共用函數
│   ├── strategy_manager.py      # 策略工廠 (Singleton + Registry)
│   ├── strategies/              # 策略實作目錄
│   │   ├── base.py              # BaseStrategy 抽象基底 (共用日期提取/大盤熔斷)
│   │   ├── v31_hybrid.py        # V31 混合策略 (V30+XGBoost)
│   │   ├── v33_low_vol.py       # V33 低波動策略
│   │   ├── v34_turbo.py         # V34 高成長策略
│   │   └── v35_innovation.py    # V35 經營效益策略（營業利益率）
│   ├── strategy.py              # V30/V31 選股邏輯 (輕量委派)
│   ├── calc_indicators.py       # 技術指標 + 特徵計算 (唯一來源)
│   ├── viz_helper.py            # Plotly 視覺化 + 回測摘要
│   ├── db_helper.py             # 資料庫操作 (唯一入口)
│   └── news_agent.py            # 情緒分析
│
├── 📦 數據與模型
│   └── ML_Data/                 # 模型 + 回測結果
│       ├── pkl/                 # XGBoost 模型 (每策略獨立檔案)
│       └── *.csv                # 回測報告
│
├── 📄 設定檔
│   ├── config.py                # 統一設定中心 (唯一真理)
│   ├── requirements.txt         # Python 依賴
│   └── docker-compose.yaml      # Docker 部署
│
└── 📝 文檔
    ├── README.md                # 本文件
    ├── UpdateList.md            # 版本更新記錄
    └── openspec/                # 開發規範
```

### 模組依賴關係

```
應用層 (app.py, 1-6_*.py 腳本)
    ↓
回測/視覺化層 (4_run_backtest.py, tool/viz_helper.py)
    ↓
策略層 (tool/strategy_manager.py → tool/strategies/base.py → V31/V33/V34/V35)
    ↓
指標層 (tool/calc_indicators.py)
    ↓
資料層 (tool/db_helper.py)
    ↓
設定層 (config.py + .env)
```

**設計原則**:
- 所有資料庫操作必須通過 `tool/db_helper.py`
- 所有技術指標計算必須通過 `tool/calc_indicators.py`（唯一真理來源）
- 所有策略繼承 `BaseStrategy`，共用日期提取與大盤熔斷邏輯
- 所有常數定義必須在 `config.py`
- Web API 回測指標統一由 `tool/viz_helper.get_backtest_summary()` 提供

---

## 📱 Line Bot 指令

| 指令 | 功能 | 說明 |
|------|------|------|
| `推薦` / `AI` | V31 混合策略 | V30 篩選 + XGBoost Top 5 |
| `V30` / `策略` | 純技術選股 | 均線突破 + 量能確認 |
| `2330` | 個股診斷 | 輸入 4 碼股票代號 |
| `設定停損 5` | 調整停損 | 範圍 1%-20% |
| `查看設定` | 查看參數 | 顯示所有策略設定 |
| `dashboard` | 儀表板連結 | 取得 Web 連結 |

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

**版本**: V35 Phase 5+ Multi-Model Pipeline (2026-02-11)  
**授權**: MIT License  
**最新變更**:
- ✅ 多模型批次訓練：每策略獨立 AI 模型（V33/V34/V35 分別訓練）
- ✅ 動態模型載入：推論時自動載入策略專屬模型 + fallback 機制
- ✅ V35 策略優化：營業利益率門檻調整為 > 6%
- ✅ V34 策略調整：營收門檻調整為 `YoY > 18%`，突破/量能條件同步優化
- ✅ V33 第二階段微調：趨勢、量能與風控參數優化，回測表現改善
- ✅ 測試模式：`FORCE_BULL_MARKET` 環境變數控制市場趨勢
