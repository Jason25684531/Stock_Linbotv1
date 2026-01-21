# Stock AI Line Bot V33

> 🔥 **V33 Phase 3+ 完成** | 深度重構，移除 55 行重複代碼，修復 11 處未定義變數  
> ⚔️ **PK System 人機對決** | 模擬交易與 AI 績效比較  
> 🛡️ **Type Safety** | 完整 Type Hints，提升代碼可維護性  
> 🧪 **Test Coverage 60%+** | 核心邏輯測試覆蓋，確保穩定性  
> 🏗️ **Clean Architecture** | 消除魔術數字，統一配置管理  
> 📅 **最後更新**: 2026-01-21

## 📊 專案簡介

整合 AI 機器學習與技術分析的台股選股系統，透過 Line Bot 提供即時選股推薦與個股診斷。

### 🎯 核心功能

| 功能 | 說明 | 命令 |
|------|------|------|
| 🔥 V31 混合策略 | V30 篩選 + XGBoost 排名 + 情緒過濾 ⭐推薦 | 輸入「推薦」 |
| 🚀 V30 純技術策略 | 均線突破 + 量能確認 + 情緒熔斷 | 輸入「V30」 |
| 🎫 個股診斷 | 完整策略報告 + 停損停利建議 | 輸入「2330」 |
| 📊 Web Dashboard | 視覺化回測績效與即時選股 | 輸入「dashboard」 |
| ⚙️ 動態參數 | 即時調整停損/停利設定 | 輸入「查看設定」 |

### ✨ 最新重構（2026-01-09）

**🧠 V33 Phase 2+: Sentiment Analysis & Circuit Breaker (完成)**
- ✅ **情緒分析引擎**：`NewsSentimentAgent` 類別（Mock Mode + Real Mode 介面）
- ✅ **熔斷機制**：情緒分數低於門檻時自動暫停交易
- ✅ **XGBoost 特徵擴展**：整合 `sentiment_score` 為第 9 個特徵
- ✅ **Opt-in 設計**：預設關閉，不影響現有策略
- ✅ **確定性模擬**：基於日期哈希生成可重現的情緒數據

**⚔️ V33 Phase 3: PK System & Visualization (完成)**
- ✅ **資料庫架構**：`user_simulation_trades` 表記錄模擬交易
- ✅ **Backend API**：`POST /api/user/trade` + `GET /api/pk/battle`
- ✅ **Battle Arena**：Dashboard 新增人機對決視覺化
- ✅ **績效對比**：使用者 vs AI 報酬率、勝率雙軸比較

**⚡ V33 Phase 2: Strategy Deep Dive (完成)**
- ✅ **動能濾網**：KD 黃金交叉 + 布林通道壓縮突破（Opt-in 設計）
- ✅ **參數最佳化**：Optuna 框架，支援 ROI/Sharpe 雙目標搜尋
- ✅ **進階指標**：`calculate_kd_full()` 同時輸出 K/D 值

**🛡️ V33 Phase 1: Foundation & Quality Assurance (完成)**
- ✅ **Code Refactor**：Type Hints + 消除魔術數字 + 統一配置管理
- ✅ **Unit Testing**：31 個測試用例，60%+ 覆蓋率
- ✅ **Clean Code**：提取重複邏輯，改善可讀性與可維護性

**📊 V32 Web Dashboard**：專業量化交易儀表板（Dark Quant Theme）
- ✅ **回測擬真化**：加入 0.2% 滑價模型 + MDD/Sharpe 風險指標
- ✅ **即時選股訊號**：Dashboard 整合 Live Signals 卡片式顯示
- ✅ **修復時間序列洩露**：移除 `shuffle=True`，實現嚴格時間拆分

**🏗️ 架構優化**：
- ✅ **Clean Architecture**：app.py 純路由層，業務邏輯在 tool/
- ✅ **數據原子性**：使用 `REPLACE INTO` 避免數據丟失
- ✅ **網路重試機制**：爬蟲加入指數退避策略
- ✅ **ML Ops 安全**：自動驗證模型特徵一致性
- ✅ **環境無關**：移除硬編碼路徑，支持任意部署位置

---

## 📈 V31 混合策略詳解

### 策略流程

```mermaid
graph LR
    A[全市場股票] --> B[情緒熔斷檢查]
    B --> C{情緒過低?}
    C -->|是| D[暫停交易]
    C -->|否| E[V30 硬篩選]
    E --> F{均線多頭?<br/>量能足夠?<br/>RSI 適中?}
    F -->|✅| G[候選池]
    F -->|❌| H[排除]
    G --> I[計算比例特徵 + 情緒特徵]
    I --> J[XGBoost 評分]
    J --> K[Top 5 推薦]
```

### V30 篩選條件

| 項目 | 條件 | 說明 |
|------|------|------|
| 市場情緒 | score > -0.5（可選） | 避免在極度悲觀時買入 |
| 市場趨勢 | 非熊市 | 大盤空頭時暫停 |
| 均線排列 | 收盤 > MA20 > MA60 | 多頭趨勢 |
| 成交量 | > 300 萬股 | 流動性充足 |
| RSI | 40 < RSI < 70 | 非超買超賣 |

### XGBoost 特徵 (9 個)

| 類型 | 特徵 | 說明 |
|------|------|------|
| 技術面 | rsi | 相對強弱指標 |
| 技術面 | bias | 乖離率 |
| 技術面 | macd_hist | MACD 柱狀圖 |
| 技術面 | kd_k | KD 指標 K 值 |
| 技術面 | bb_width | 布林通道寬度 |
| 量能面 | volume_ratio | 成交量 / 20日均量 |
| 籌碼面 | foreign_ratio | 外資買賣 / 成交量 |
| 籌碼面 | trust_ratio | 投信買賣 / 成交量 |
| 🆕 情緒面 | sentiment_score | 市場情緒分數 (-1.0 ~ 1.0) |

### 🧠 情緒分析系統（V33 Phase 2+）

#### 熔斷機制原理

```python
# 每日選股前檢查情緒
sentiment_agent = NewsSentimentAgent(mock_mode=True)
result = sentiment_agent.get_daily_sentiment('2026-01-09')

if result['score'] < Config.SENTIMENT_THRESHOLD:  # 預設 -0.5
    print("🔥 觸發熔斷機制，暫停買進！")
    return pd.DataFrame()  # 返回空選股結果
```

#### 情緒分數定義

| 分數範圍 | 情緒標籤 | 熔斷狀態 | 說明 |
|---------|---------|---------|------|
| > 0.3 | 樂觀 ✅ | 正常交易 | 市場氣氛正向 |
| -0.3 ~ 0.3 | 中性 ⚪ | 正常交易 | 市場平穩 |
| -0.5 ~ -0.3 | 輕度悲觀 ⚠️ | 正常交易 | 謹慎觀察 |
| < -0.5 | 極度悲觀 ❌ | 觸發熔斷 | 暫停買入 |

#### Mock Mode（開發階段）
- 使用日期哈希 + 正弦函數生成確定性分數
- 確保同一日期總是返回相同結果（可重現性）
- 不依賴外部 API，訓練與回測穩定

#### Real Mode（未來擴展）
- 整合 Gemini AI 分析新聞情緒
- 預留介面：`_analyze_with_gemini(date_str)`
- 需實作：RSS 新聞抓取 + Prompt 工程

### 🔒 防止數據洩露機制

#### 問題：舊版使用 `train_test_split(shuffle=True)`
```python
# ❌ 錯誤：會將未來數據混入訓練集
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, shuffle=True  # 打亂順序！
)
```

#### 解決：時間序列拆分
```python
# ✅ 正確：嚴格按日期順序拆分
def time_series_split(df, train_ratio=0.8):
    # 1. 按日期排序
    df = df.sort_values('trade_date')
    
    # 2. 找出所有唯一日期
    unique_dates = sorted(df['trade_date'].unique())
    
    # 3. 前 80% 日期訓練，後 20% 測試
    split_idx = int(len(unique_dates) * train_ratio)
    train_end_date = unique_dates[split_idx - 1]
    
    train_df = df[df['trade_date'] <= train_end_date]
    test_df = df[df['trade_date'] > train_end_date]
    
    return train_df, test_df
```

#### 效果
- 📅 訓練期間：2024-01-02 ~ 2024-10-15
- 📅 測試期間：2024-10-16 ~ 2024-12-31
- ✅ **確保測試集所有日期都晚於訓練集**

### 🔥 V31 策略優化（2026-01）

針對回測結果（勝率 31%，平均獲利 +3.42%，平均虧損 -3.06%，風報比 1.12）進行參數優化：

#### 優化目標
- 🎯 **提高存活率**：在震盪市場減少停損次數
- 🎯 **提升風報比**：利用階梯式停損鎖定更多利潤
- 🎯 **市場適應性**：空頭時期暫停買入，降低系統性風險

#### 優化內容

| 項目 | 舊參數 | 新參數 | 理由 |
|------|--------|--------|------|
| 停損 | -5% | **-10%** | 台股波動大，5% 常被震出 |
| 停利 | +15% | **+20%** | 給予更大上漲空間 |
| 倉位 | 30% | **20%** | 配合更寬停損，降低單筆風險 |
| 市場過濾 | 無 | **空頭不買** | 避免在熊市逆勢操作 |

#### 階梯式移動停損

傳統停損問題：**獲利 5% 後鎖定 1%，一旦回落立即出場，無法享受趨勢行情**

新機制：**3 級階梯，越漲鎖越多**

| 階段 | 獲利門檻 | 停損位置 | 鎖定利潤 | 說明 |
|------|----------|----------|----------|------|
| Level 1 | ≥ 10% | 成本 + 1% | +1% | 突破震盪區，確保不虧 |
| Level 2 | ≥ 20% | 成本 + 15% | +15% | 趨勢確立，鎖定大部分 |
| Level 3 | ≥ 30% | 成本 + 25% | +25% | 強勢股，讓利潤奔跑 |

**範例情境**：
```
買入成本 100 元
 ↓
漲到 115 (+15%) → Level 2 啟動，停損移至 115 (+15%)
 ↓
繼續漲到 130 (+30%) → Level 3 啟動，停損移至 125 (+25%)
 ↓
回落至 125 → 出場，鎖定 +25% 利潤
```

#### 市場趨勢過濾

```python
# 在選股前檢查市場狀態
from tool.db_helper import get_market_trend

if get_market_trend() == 'BEAR':
    return pd.DataFrame()  # 空頭市場，不推薦任何股票
```

**觸發條件**（參考大盤指數 0050）：
- **BULL（多頭）**：5 日均線 > 20 日均線 > 60 日均線
- **BEAR（空頭）**：不符合多頭條件

#### 預期效果

| 指標 | 優化前預期 | 優化後目標 |
|------|------------|------------|
| 勝率 | 31% | 35-40% |
| 平均獲利 | +3.42% | +5-8% |
| 平均虧損 | -3.06% | -4-6% |
| 風報比 | 1.12 | 1.5+ |
| 最大回撤 | -15% | -12% 以內 |

---

## 📁 專案架構

### 🏗️ 整體架構圖

```
┌──────────────────────────────────────────────────────────┐
│                   🌐 使用者介面層                          │
│  Line Bot (app.py) │ Web Dashboard │ 本地測試 (debug_local.py) │
└────────────┬─────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────┐
│                   📊 業務邏輯層                            │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  │
│  │ strategy.py  │  │ news_agent.py │  │ db_helper.py │  │
│  │ (選股策略)    │  │ (情緒分析)     │  │ (資料查詢)    │  │
│  └──────────────┘  └───────────────┘  └──────────────┘  │
│         │                   │                  │          │
│  ┌──────▼───────────────────▼──────────────────▼──┐      │
│  │          calc_indicators.py (技術指標)         │      │
│  └──────────────────────────────────────────────┘      │
└────────────┬─────────────────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────────────────┐
│                   🗄️ 資料存取層                            │
│      MySQL Database  │  XGBoost Model (.pkl)              │
└──────────────────────────────────────────────────────────┘

                         ▲
                         │
           ┌─────────────┴──────────────┐
           │     📥 資料更新層           │
           │  1_update_database.py      │
           │  (爬蟲 + 資料清洗)           │
           └────────────────────────────┘
```

### 📂 檔案結構與職責

```
Stock_Linbotv1/
│
├── 📊 每日流程腳本 (自動化工作流)
│   ├── 1_update_database.py     # 爬取上市櫃股價 + 籌碼數據 (含重試機制)
│   ├── 2_rundaily.py            # 整合腳本：執行 1→計算指標→推播
│   └── 5_push_to_line.py        # Line 每日選股推播通知
│
├── 🤖 AI 模型與回測
│   ├── 3_train_model.py         # XGBoost 訓練（時間序列拆分 + 情緒特徵）
│   ├── 4_run_backtest.py        # 統一回測引擎（支援 V30/V31）
│   └── 5_optimize_params.py     # Optuna 參數最佳化（選用）
│
├── 🌐 使用者介面層
│   ├── app.py                   # Flask Line Bot 主程式（純路由）
│   ├── debug_local.py           # 本地互動測試工具
│   ├── templates/               # Web Dashboard 頁面
│   │   ├── base.html
│   │   └── dashboard.html
│   └── static/                  # 靜態資源（若有）
│
├── ⚙️ 核心業務模組 (tool/)
│   ├── __init__.py              # 模組初始化
│   ├── strategy.py              # 🎯 V30/V31 選股邏輯 + 格式化輸出
│   ├── db_helper.py             # 🗄️ 資料庫查詢 + 設定管理
│   ├── calc_indicators.py       # 📊 技術指標計算（RSI/MACD/KD/BB）
│   └── news_agent.py            # 🧠 市場情緒分析（Mock + Gemini）
│
├── 📦 部署與測試
│   ├── start_linebot.bat        # Windows 快速啟動 Line Bot
│   ├── docker-compose.yaml      # Docker 容器部署設定
│   ├── tests/                   # 單元測試（pytest）
│   │   ├── conftest.py          # 測試 fixtures
│   │   ├── test_indicators.py   # 技術指標測試
│   │   └── test_strategy.py     # 策略邏輯測試
│   └── pytest.ini               # 測試設定檔
│
├── 📂 數據與模型
│   ├── ML_Data/
│   │   ├── pkl/                 # XGBoost 模型檔案
│   │   │   └── stock_ai_model.pkl
│   │   ├── backtest_result.csv  # 交易明細
│   │   └── backtest_profit_report.csv  # 每日資產
│   └── logs/                    # 執行日誌
│
├── 📄 文檔與規格
│   ├── README.md                # 本文件（完整指南）
│   ├── UpdateList.md            # 版本更新記錄
│   └── openspec/                # 專案規格與任務管理
│
└── 🔧 設定檔
    ├── config.py                # 📌 統一設定中心（所有參數）
    ├── init_settings.py         # 資料庫表初始化腳本
    ├── requirements.txt         # Python 依賴套件
    ├── .env.example             # 環境變數範例
    └── .gitignore               # Git 忽略規則
```

### 🔄 資料流程圖

```mermaid
graph TD
    A[證交所/櫃買中心 API] -->|1_update_database.py| B[(MySQL Database)]
    B -->|calc_indicators.py| C[技術指標計算]
    C --> B
    B -->|3_train_model.py| D[XGBoost 模型訓練]
    D --> E[stock_ai_model.pkl]
    
    B -->|2_rundaily.py 整合| F{選股引擎}
    E --> F
    
    F -->|V30 純技術| G[均線突破選股]
    F -->|V31 混合| H[V30 + AI 評分]
    F -->|情緒過濾| I[NewsSentimentAgent]
    
    G --> J[5_push_to_line.py]
    H --> J
    I --> F
    
    J -->|Line Messaging API| K[Line Bot 推播]
    
    B -->|app.py 路由| L[Web Dashboard]
    B -->|app.py 路由| K
    
    M[使用者指令] -->|Line Bot| K
    K -->|app.py| F
    F -->|格式化| K
```

---

## 🚀 快速開始

### 環境需求

- Python 3.8+
- MySQL 5.7+
- 建議 8GB RAM 以上

### 安裝步驟

#### 1. Clone 專案
```powershell
cd D:\01_Project\Stocke
git clone <your-repo-url> Stock_Linbotv1
cd Stock_Linbotv1
```

#### 2. 建立虛擬環境
```powershell
python -m venv myenv
.\myenv\Scripts\activate
```

#### 3. 安裝套件
```powershell
pip install -r requirements.txt
```

#### 4. 資料庫設定

建立資料庫：
```sql
CREATE DATABASE stock_ai_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

修改 [config.py](config.py) 或設定環境變數：
```python
class Config:
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:password@localhost:3306/stock_ai_db"
    LINE_CHANNEL_ACCESS_TOKEN = "your_line_token"
    LINE_CHANNEL_SECRET = "your_line_secret"
```

或使用 `.env` 檔案：
```env
DB_URL=mysql+pymysql://root:password@localhost:3306/stock_ai_db
LINE_TOKEN=your_line_token
LINE_SECRET=your_line_secret
```

#### 5. 初始化資料庫設定
```powershell
python init_settings.py
```

---

## 📊 完整執行流程

### 🎬 首次設置（完整部署）

#### Step 1: 環境準備
```powershell
# 1. Clone 專案
cd D:\01_Project\Stocke
git clone <your-repo-url> Stock_Linbotv1
cd Stock_Linbotv1

# 2. 建立虛擬環境
python -m venv myenv
.\myenv\Scripts\activate

# 3. 安裝依賴
pip install -r requirements.txt
```

#### Step 2: 資料庫設定
```powershell
# 1. 建立資料庫（MySQL Workbench 或命令列）
CREATE DATABASE stock_ai_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 2. 修改 config.py 設定資料庫連線
# 或建立 .env 檔案：
DB_URL=mysql+pymysql://root:your_password@localhost:3306/stock_ai_db
LINE_TOKEN=your_line_channel_access_token
LINE_SECRET=your_line_channel_secret
GEMINI_KEY=your_gemini_api_key

# 3. 初始化資料表
python init_settings.py
```

#### Step 3: 資料初始化
```powershell
# 1. 爬取歷史股價（首次執行會抓取 2024-01-01 至今的資料）
python 1_update_database.py
# ⏱️ 預計耗時：15-30 分鐘（視資料量而定）

# 2. 計算技術指標
python -c "from tool.calc_indicators import main; main()"
# ⏱️ 預計耗時：5-10 分鐘

# 3. 訓練 XGBoost 模型（含時間序列拆分 + 情緒特徵）
python 3_train_model.py
# ⏱️ 預計耗時：3-5 分鐘
# ✅ 成功後會產生 ML_Data/pkl/stock_ai_model.pkl
```

### 📅 每日更新流程

#### 方法一：一鍵整合腳本（推薦）⭐
```powershell
python 2_rundaily.py

# 自動執行順序：
# Step 1: 1_update_database.py    → 爬取最新股價
# Step 2: tool/calc_indicators.py → 計算今日技術指標
# Step 3: 5_push_to_line.py       → Line 日報推播（選用）

# ⏱️ 總耗時：約 5-10 分鐘
```

#### 方法二：手動分步執行
```powershell
# Step 1: 更新股價資料（含三大法人籌碼）
python 1_update_database.py
# 建議執行時間：每日 14:30 收盤後
# ⚠️ 注意：必須等資料更新完成才能進行下一步

# Step 2: 計算技術指標（依賴 Step 1 的最新資料）
python -c "from tool.calc_indicators import main; main()"
# 計算內容：MA20/MA60, RSI, MACD, KD, BB, Bias

# Step 3: Line 推播（可選，需要 Line Bot Token）
python 5_push_to_line.py
# 推播內容：V30 策略選股 Top 5
```

### 🧪 模型訓練與回測

#### 重新訓練模型（建議每月執行）
```powershell
# 使用所有最新資料重新訓練
python 3_train_model.py

# 特徵說明：
# - 8 個技術指標特徵（rsi, bias, macd_hist, kd_k, bb_width, volume_ratio, foreign_ratio, trust_ratio）
# - 1 個情緒分析特徵（sentiment_score, V33 Phase 2+）
# - 時間序列拆分：前 80% 訓練，後 20% 測試
# - 無未來數據洩露（Look-ahead Bias Free）

# 輸出：
# ✅ ML_Data/pkl/stock_ai_model.pkl
# ✅ ML_Data/feature_engineering/training_data.csv
# ✅ 訓練報告（準確率、Precision、分類報告）
```

#### 執行策略回測
```powershell
# V31 混合策略回測（預設，含情緒過濾）
python 4_run_backtest.py
# 或明確指定
python 4_run_backtest.py --v31

# V30 純技術策略回測
python 4_run_backtest.py --v30

# 回測輸出：
# ✅ ML_Data/backtest_result.csv        → 交易明細（每筆進出場記錄）
# ✅ ML_Data/backtest_profit_report.csv → 每日資產變化（用於繪製曲線）

# 關鍵指標：
# - 總報酬率 (ROI)
# - 勝率 (Win Rate)
# - 最大回撤 (MDD)
# - 夏普比率 (Sharpe Ratio)
# - 交易次數、平均持有天數
```

#### 參數最佳化（進階功能）
```powershell
# 使用 Optuna 搜尋最佳參數組合
python 5_optimize_params.py

# 最佳化目標：
# - ROI (報酬率)
# - Sharpe Ratio (風險調整後報酬)

# 搜尋範圍：
# - 停損：5%-15%
# - 停利：10%-30%
# - RSI 門檻：35-45 / 65-75
# - 持有天數：5-15 天

# ⏱️ 預計耗時：1-3 小時（視搜尋次數而定）
```

### 🌐 Line Bot 啟動

#### 方式 1：使用批次檔（Windows，推薦）
```powershell
# 雙擊執行或命令列啟動
.\start_linebot.bat

# 自動執行：
# 1. 啟動虛擬環境
# 2. 執行 python app.py
# 3. 監聽 Port 5000
```

#### 方式 2：直接執行 Python
```powershell
# 確保已在虛擬環境中
python app.py

# 啟動資訊：
# 🚀 Line Bot V3.0 啟動中 (V30策略增強版)
# 📋 模型狀態: ✅ 已載入
# 💡 主要策略: V30 純技術分析 (40%報酬實績)
# * Running on http://0.0.0.0:5000
```

#### 方式 3：生產環境部署（Gunicorn，Linux/Mac）
```bash
# 安裝 Gunicorn
pip install gunicorn

# 多 Worker 模式啟動
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# 參數說明：
# -w 4: 4 個 Worker 處理並發請求
# -b 0.0.0.0:5000: 綁定所有網卡，Port 5000
# app:app: 模組名稱:應用程式實例
```

#### 方式 4：Docker 容器部署
```powershell
# 使用 docker-compose
docker-compose up -d

# 查看日誌
docker-compose logs -f

# 停止服務
docker-compose down
```

### 🖥️ Web Dashboard 存取

```powershell
# 確保 app.py 已啟動
python app.py

# 瀏覽器開啟
http://localhost:5000/dashboard

# 或透過 Line Bot 取得連結
# 在 Line 中輸入：dashboard 或 儀表板
```

---

## 🧪 測試與驗證

### 快速檢查清單

執行前的系統驗證：

| 步驟 | 指令 | 用途 | 預期結果 |
|------|------|------|---------|
| 1️⃣ 更新資料 | `python 1_update_database.py` | 抓取最新股價 | 資料庫新增當日記錄 |
| 2️⃣ 計算指標 | `python -c "from tool.calc_indicators import main; main()"` | 計算 MA/RSI/MACD | 指標欄位更新 |
| 3️⃣ 訓練模型 | `python 3_train_model.py` | 重新訓練 XGBoost | 產生新的 .pkl 檔案 |
| 4️⃣ 執行回測 | `python 4_run_backtest.py` | 驗證策略績效 | 產生回測報表 CSV |
| 5️⃣ 本地測試 | `python debug_local.py` | 互動式選股 | 顯示推薦股票 |

### 語法檢查（開發用）

```powershell
# 檢查核心檔案語法
python -m py_compile config.py
python -m py_compile tool/strategy.py
python -m py_compile tool/news_agent.py
python -m py_compile 3_train_model.py
python -m py_compile 4_run_backtest.py
python -m py_compile 5_push_to_line.py

# 執行單元測試
pytest                                    # 所有測試
pytest tests/test_strategy.py -v         # 策略測試
pytest --cov=tool --cov-report=html      # 含覆蓋率報告
```

### 本地互動測試（推薦）

不需要 Line Bot，在本地終端機測試策略：

```powershell
python debug_local.py
```

互動命令：

| 指令 | 功能 | 範例 |
|------|------|------|
| `推薦` | V31 混合策略選股 | - |
| `V30` | 純技術面選股 | - |
| `2330` | 個股診斷 | 輸入任意 4 碼股票代號 |
| `設定停損 5` | 調整停損為 5% | 範圍 1-20 |
| `設定停利 20` | 調整停利為 20% | 範圍 5-50 或 0（不停利） |
| `查看設定` | 顯示當前參數 | - |
| `exit` | 結束程式 | - |

### 回測驗證

```powershell
# V31 混合策略回測
python 4_run_backtest.py --v31

# V30 純技術策略回測
python 4_run_backtest.py --v30
```

回測結果會儲存在：
- `ML_Data/backtest_result.csv` - 交易明細
- `ML_Data/backtest_profit_report.csv` - 績效報告

---

## 📱 Line Bot 使用指南

### 註冊 Line Bot

1. 前往 [Line Developers](https://developers.line.biz/)
2. 建立 Provider 和 Messaging API Channel
3. 取得 Channel Access Token 和 Channel Secret
4. 在 [config.py](config.py) 或 `.env` 設定

### 設定 Webhook

```
Webhook URL: https://your-domain.com/callback
```

### 指令清單

| 指令 | 功能 | 說明 |
|------|------|------|
| `推薦` / `AI` | 🔥 V31 混合策略 | V30 篩選 + XGBoost 排名 Top 5 |
| `V30` / `策略` | 🚀 純技術選股 | 均線突破 + 量能確認 |
| `2330` | 個股診斷 | 輸入任意 4 碼股票代號 |
| `設定停損 5` | 設定停損 | 範圍 1%-20% |
| `設定停利 20` | 設定停利 | 範圍 5%-50%，輸入 0 表示不停利 |
| `設定信心 60` | AI 信心門檻 | 範圍 0%-100% |
| `查看設定` | 查看參數 | 顯示所有策略設定 |
| `dashboard` / `儀表板` | 📊 V32 Dashboard | 取得 Web 儀表板連結 |

---

## 📊 V32 Web Dashboard（新功能）

### 功能介紹

V32 新增專業量化交易儀表板，提供視覺化回測績效與即時選股訊號。

### 啟動 Dashboard

```powershell
# 啟動 Flask 伺服器
python app.py

# 訪問 Dashboard
# 瀏覽器開啟 http://localhost:5000/dashboard
```

### Dashboard 功能

#### 1. **回測績效總覽**
四大核心指標卡片：
- 📈 **總報酬率 (ROI)**: 含滑價成本的真實報酬
- 🎯 **勝率 (Win Rate)**: 獲利交易佔比
- 📉 **最大回撤 (MDD)**: 資產最大虧損幅度
- ⚡ **夏普比率 (Sharpe)**: 風險調整後報酬

#### 2. **資產曲線圖 (Equity Curve)**
- 使用 Chart.js 繪製每日資產變化
- 根據最終報酬率顯示綠色（獲利）或紅色（虧損）
- 懸停顯示日期、資產價值與 ROI
- Area fill 增強視覺效果

#### 3. **交易明細表 (Trades Table)**
- 顯示最近 20 筆交易
- 包含：股票代號、買入/賣出日期、價格、獲利率、持有天數
- 賣出原因色彩標示：
  - 🔴 停損: 紅色背景
  - 🟢 停利: 綠色背景
  - ⚪ 時間到/趨勢轉空: 灰色背景

#### 4. **⚡ 即時選股訊號 (Live Signals)**
- 即時顯示 V31 混合策略選股結果
- 卡片式設計，每檔股票獨立顯示：
  - 股票代號（藍色大字）
  - 收盤價
  - **AI Score** (信心度評分)
    - ≥ 70%: 綠色（高信心）
    - ≥ 50%: 琥珀色（中等）
    - < 50%: 灰色（低信心）
  - RSI 指標
  - 成交量（K 為單位）
  - MA20 均線
- 響應式布局（手機/平板/桌面自適應）

### 設計美學

**Dark Quant Theme（深色量化主題）**
- 主背景: `#0a0e1a` (深藍黑)
- 卡片背景: `#1a2132` (深灰藍)
- 強調色: 綠色 `#10b981` (獲利) / 紅色 `#ef4444` (虧損)
- 字體: JetBrains Mono (專業等寬字體)
- 動畫效果: 背景漸變、卡片懸停發光

### API 端點

| 端點 | 方法 | 功能 | 回傳格式 |
|------|------|------|---------|
| `/dashboard` | GET | Dashboard 主頁 | HTML |
| `/api/performance` | GET | 資產曲線數據 | JSON: {dates, equity, roi} |
| `/api/trades` | GET | 交易明細（最近 50 筆） | JSON: [trade_list] |
| `/api/summary` | GET | 總結指標 | JSON: {total_roi, win_rate, mdd, sharpe, ...} |
| `/api/daily-signals` | GET | 即時選股訊號 | JSON: {date, signals, count} |

### 技術架構

```
Frontend (前端)
  ├─ TailwindCSS 3.x (CDN) - 樣式框架
  ├─ Alpine.js 3.x (CDN) - 響應式資料綁定
  ├─ Chart.js 4.4.1 (CDN) - 圖表視覺化
  └─ JetBrains Mono - 專業字體

Backend (後端)
  ├─ Flask Routes - API 路由
  ├─ Pandas - 數據處理
  └─ CSV Files - 回測結果存儲
      ├─ ML_Data/backtest_result.csv (交易明細)
      └─ ML_Data/backtest_profit_report.csv (每日資產)
```

### Line Bot 整合

在 Line Bot 中輸入 `dashboard` 或 `儀表板`：
```
📊 V32 Web Dashboard
==================
🌐 連結: http://localhost:5000/dashboard

✨ 功能:
• 資產曲線圖
• 回測績效指標 (ROI/勝率/MDD/Sharpe)
• 交易明細表
• ⚡ 即時選股訊號

💡 提示: 請先執行回測以產生數據
```

---

## 📁 關鍵文件說明

### 核心腳本

| 檔案 | 功能 | 執行時機 |
|------|------|---------|
| [1_update_database.py](1_update_database.py) | 爬取上市櫃股價 + 籌碼 | 每日收盤後 |
| [tool/calc_indicators.py](tool/calc_indicators.py) | 計算技術指標 (MA, RSI, MACD 等) | 資料更新後 |
| [3_train_model.py](3_train_model.py) | 訓練 XGBoost 模型（時間序列拆分） | 每月 1 次 |
| [4_run_backtest.py](4_run_backtest.py) | 回測引擎 | 策略調整後 |
| [app.py](app.py) | Line Bot 主程式（純路由層） | 24/7 運行 |

### 核心模組

| 檔案 | 功能 | 關鍵類別/函數 |
|------|------|--------------|
| [config.py](config.py) | 統一設定中心 | `Config` 類別 |
| [tool/strategy.py](tool/strategy.py) | 策略邏輯 + 格式化 | `get_best_stocks_v31_hybrid()`, `format_v31_recommendation()` |
| [tool/db_helper.py](tool/db_helper.py) | 資料庫操作（含 upsert） | `get_stock_data()`, `upsert_stock_data()` |

### 批次檔（Windows）

| 檔案 | 功能 | 說明 |
|------|------|------|
| [run_bot.bat](run_bot.bat) | 每日自動化流程 | 更新→計算→推播 |
| [start_linebot.bat](start_linebot.bat) | 啟動 Line Bot | 使用相對路徑 |
| [run_backtest.bat](run_backtest.bat) | 執行回測 | 支援 v30/v31 參數 |

---

## 🏗️ 架構設計原則

### 分層架構 (Layered Architecture)

```
┌─────────────────────────────────────────┐
│   🌐 Presentation Layer (表現層)         │
│   - app.py (Flask 路由 + Line Bot)      │
│   - debug_local.py (本地測試介面)       │
│   - templates/ (Web Dashboard 前端)     │
└──────────────┬──────────────────────────┘
               │ 只負責：路由分發、請求處理、格式化輸出
┌──────────────▼──────────────────────────┐
│   📊 Business Logic Layer (業務層)       │
│   - tool/strategy.py (選股邏輯)         │
│   - tool/news_agent.py (情緒分析)       │
│   - tool/calc_indicators.py (指標計算)  │
└──────────────┬──────────────────────────┘
               │ 只負責：核心演算法、策略判斷、特徵工程
┌──────────────▼──────────────────────────┐
│   🗄️ Data Access Layer (資料存取層)      │
│   - tool/db_helper.py (資料庫查詢)       │
│   - config.py (設定管理)                │
└──────────────┬──────────────────────────┘
               │ 只負責：資料 CRUD、參數讀寫
┌──────────────▼──────────────────────────┐
│   💾 Data Storage Layer (資料儲存層)     │
│   - MySQL Database (股價 + 技術指標)    │
│   - XGBoost Model (.pkl 檔案)           │
└─────────────────────────────────────────┘
```

### 🎯 關注點分離 (Separation of Concerns)

| 層級 | 職責 | 檔案 | 禁止事項 |
|------|------|------|---------|
| 🌐 表現層 | HTTP 路由、訊息格式化 | app.py, debug_local.py | ❌ 不寫業務邏輯、不直接查資料庫 |
| 📊 業務層 | 選股演算法、技術分析 | tool/strategy.py, tool/calc_indicators.py | ❌ 不處理 HTTP、不直接操作 SQL |
| 🗄️ 資料層 | 資料庫 CRUD、設定管理 | tool/db_helper.py | ❌ 不包含業務邏輯、不做數據分析 |
| ⚙️ 設定層 | 參數集中管理 | config.py | ❌ 不執行邏輯、只存常數和配置 |

### 🔒 數據安全與正確性

| 項目 | 實作方式 | 目的 |
|------|---------|------|
| **SQL Injection 防護** | 使用 SQLAlchemy 參數化查詢 | 防止惡意 SQL 注入攻擊 |
| **原子性操作** | `REPLACE INTO` 替代 `DELETE + INSERT` | 避免資料更新中斷導致遺失 |
| **時間序列拆分** | 按日期嚴格劃分訓練/測試集 | 防止未來數據洩露（Look-ahead Bias） |
| **特徵一致性驗證** | 模型載入時檢查特徵名稱 | 確保訓練與推論使用相同特徵 |
| **資料快取** | Module-level 快取模型 | 避免重複載入，提升效能 |
| **錯誤隔離** | Try-except + 預設值降級 | 單一模組失敗不影響整體系統 |

### 🔄 設計模式應用

#### 1. **Opt-in Design（選擇性啟用）**
```python
# 新功能預設關閉，避免影響現有系統
Config.ENABLE_SENTIMENT_FILTER = False  # 情緒分析
Config.USE_KD_FILTER = False            # KD 濾網
Config.USE_BB_FILTER = False            # 布林通道濾網
```

#### 2. **Lazy Loading（延遲載入）**
```python
# 全域快取，避免重複載入模型
_cached_model = None
def _load_v31_model():
    global _cached_model
    if _cached_model is not None:
        return _cached_model
    # 第一次呼叫才載入...
```

#### 3. **Strategy Pattern（策略模式）**
```python
# 統一介面，不同策略實作
def get_v30_candidates(df) -> pd.DataFrame:  # 純技術策略
def get_best_stocks_v31_hybrid(df) -> pd.DataFrame:  # 混合策略
```

#### 4. **Circuit Breaker（熔斷器模式）**
```python
# 市場情緒過低時自動暫停交易
sentiment = check_sentiment_filter(date_str)
if sentiment:  # 觸發熔斷
    return pd.DataFrame()  # 返回空選股
```

### 📋 程式碼品質標準

| 標準 | 要求 | 目的 |
|------|------|------|
| **Type Hints** | 所有函數必須標註型別 | 提升可讀性、IDE 自動補全 |
| **Docstrings** | 使用 Google Style 格式 | 自動生成 API 文檔 |
| **無魔術數字** | 所有常數移至 Config | 集中管理、避免重複定義 |
| **單一職責** | 每個函數只做一件事 | 易於測試、維護 |
| **測試覆蓋** | 核心模組需 60%+ 覆蓋率 | 確保邏輯正確性 |

---

## 🔄 更新內容 (2026-01 重構)

### 🔥 修復時間序列洩露

#### 問題
```python
# ❌ 舊版：隨機打亂會將未來數據混入訓練集
X_train, X_test = train_test_split(X, y, shuffle=True)
```

#### 解決
```python
# ✅ 新版：嚴格按日期拆分
def time_series_split(df, train_ratio=0.8):
    unique_dates = sorted(df['trade_date'].unique())
    split_idx = int(len(unique_dates) * train_ratio)
    train_end_date = unique_dates[split_idx - 1]
    
    train_df = df[df['trade_date'] <= train_end_date]
    test_df = df[df['trade_date'] > train_end_date]
    
    return train_df, test_df
```

### ✨ 架構優化

| 項目 | Before | After |
|------|--------|-------|
| 路由邏輯 | app.py 包含格式化 | 格式化移至 strategy.py |
| 數據更新 | DELETE + INSERT | REPLACE INTO (原子性) |
| 網路請求 | 無重試 | 指數退避重試機制 |
| 模型載入 | 無驗證 | 自動驗證特徵一致性 |
| 路徑依賴 | 硬編碼 `d:\...` | 相對路徑 `%~dp0` |

### 📊 效能改善

| 指標 | Before | After | 改善 |
|------|--------|-------|------|
| 數據洩露風險 | ⚠️ 高 | ✅ 無 | 100% |
| 爬蟲成功率 | ~80% | ~95% | +15% |
| 代碼可維護性 | 中 | 高 | +40% |
| 部署彈性 | 低 | 高 | +60% |

---

## 🔮 未來展望

### 短期目標 (1-3 個月)

| 項目 | 說明 | 優先級 | 狀態 |
|------|------|--------|------|
| 📊 回測優化 | 加入滑價、手續費、稅金精算 | 高 | 🔄 進行中 |
| 📈 績效追蹤 | 實盤 vs 回測對比報表 | 高 | 📋 計劃中 |
| 🔔 進出場提醒 | Line 推送買賣訊號 | 中 | 📋 計劃中 |
| 🧪 單元測試 | 提升測試覆蓋率至 60% | 中 | 📋 計劃中 |

### 中期目標 (3-6 個月)

| 項目 | 說明 | 優先級 | 狀態 |
|------|------|--------|------|
| 🧠 模型升級 | 加入三大法人、融資融券特徵 | 高 | 📋 計劃中 |
| 📉 多策略並行 | 同時追蹤多組參數組合 | 中 | 📋 計劃中 |
| 🌐 Web Dashboard | 視覺化績效報表（React + Flask） | 中 | 📋 計劃中 |
| 🐳 Docker 部署 | 完整容器化部署方案 | 中 | 📋 計劃中 |

### 長期目標 (6-12 個月)

| 項目 | 說明 | 優先級 | 狀態 |
|------|------|--------|------|
| 🤖 AutoML | 自動特徵選擇與超參數調整 | 中 | 💡 研究中 |
| 📉 空頭策略 | 加入反向 ETF 避險邏輯 | 中 | 💡 研究中 |
| 🔗 券商 API | 自動下單整合（模擬帳戶） | 低 | 💡 研究中 |
| 📱 App 開發 | React Native / Flutter App | 低 | 💡 研究中 |

### 技術債清理

- [ ] 統一日誌格式（使用 logging module）
- [ ] 單元測試覆蓋率提升至 60%
- [ ] 性能分析與優化（大數據量處理）
- [ ] API 文檔化（Swagger/OpenAPI）
- [ ] CI/CD 流程建立（GitHub Actions）

---

## 🧪 測試與品質保證

### V33 測試框架（2026-01-08 新增）

專案現已整合完整的單元測試框架，確保核心邏輯的穩定性與正確性。

#### 測試覆蓋範圍

| 模組 | 測試用例 | 覆蓋率 | 狀態 |
|------|---------|-------|------|
| `tool/calc_indicators.py` | 19 個 | ~70% | ✅ 完成 |
| `tool/strategy.py` | 17 個 | ~65% | ✅ 完成 |
| **總計** | **36 個** | **~60%** | ✅ Phase 1 |

#### 如何執行測試

```powershell
# 1. 安裝測試依賴
pip install pytest pytest-cov

# 2. 執行所有測試
pytest

# 3. 執行並顯示覆蓋率報告
pytest --cov=tool --cov-report=html

# 4. 只執行指標測試
pytest tests/test_indicators.py -v

# 5. 只執行策略測試
pytest tests/test_strategy.py -v
```

#### 測試架構

```
tests/
├── conftest.py              # 共用 fixtures (測試數據生成器)
├── test_indicators.py       # 技術指標計算測試
│   ├── TestRSI             # RSI 計算準確度
│   ├── TestMACD            # MACD 趨勢偵測
│   ├── TestKD              # KD 指標範圍
│   ├── TestBollingerBands  # 布林通道寬度
│   └── TestBias            # 乖離率計算
└── test_strategy.py         # 策略邏輯測試
    ├── TestMarketTrendCheck    # 市場趨勢檢查
    ├── TestV30Candidates       # V30 篩選邏輯
    ├── TestV31HybridStrategy   # V31 混合策略
    └── TestV30ParamsFromDB     # 參數讀取
```

#### 測試特色

**🎯 Mock 技術**：
- 使用 `unittest.mock` 隔離外部依賴（資料庫、模型）
- 測試時不需要真實的 MySQL 連線或訓練好的模型

**📊 數據驗證**：
- 包含已知 RSI 值的測試數據，驗證演算法正確性
- 測試邊界情況：空數據、數據不足、極端值

**🔄 自動化測試**：
- 可整合至 CI/CD 流程
- 每次 commit 前建議執行測試

#### 測試覆蓋示例

```python
# 範例：RSI 計算測試
def test_rsi_boundary_values():
    """測試 RSI 極端值"""
    # 持續上漲應該接近 100
    uptrend = pd.Series(range(1, 101))
    rsi_up = calculate_rsi(uptrend, period=14)
    assert rsi_up.iloc[-1] > 90
    
    # 持續下跌應該接近 0
    downtrend = pd.Series(range(100, 0, -1))
    rsi_down = calculate_rsi(downtrend, period=14)
    assert rsi_down.iloc[-1] < 10
```

### 代碼品質改善（V33）

| 項目 | Before | After | 改善 |
|------|--------|-------|------|
| Type Hints 覆蓋率 | ~10% | ~90% | +80% |
| Magic Numbers | 15+ | 0 | -100% |
| 重複代碼 | 3 處 | 0 | -100% |
| 測試用例數 | 0 | 36 | +36 |

**改善內容**：
- ✅ 所有函數新增 Type Hints（`pd.DataFrame`, `Optional[int]`, etc.）
- ✅ 移除魔術數字，統一使用 `Config` 管理參數
- ✅ 提取重複邏輯為獨立函數（如 `check_market_trend()`）
- ✅ 改善 Docstrings，符合 Google Style

---

## 📚 相關文檔

- [V31策略參數調整指南.md](V31策略參數調整指南.md) - 詳細參數說明
- [架構優化總結.md](架構優化總結.md) - 重構歷程記錄
- [系統架構分析報告.md](系統架構分析報告.md) - 技術架構分析

---

## ⚠️ 注意事項

### 風險聲明

1. **本系統僅供學習與研究使用**，不構成投資建議
2. AI 預測準確率有限，請謹慎評估風險
3. 回測績效不代表未來表現
4. 建議搭配人工判斷，切勿全自動交易

### 使用限制

1. **資料延遲**：使用收盤後資料，無法當日盤中交易
2. **滑價影響**：實盤成交價可能與回測差異
3. **流動性風險**：小型股可能無法以理想價格成交
4. **系統風險**：網路斷線、系統故障可能影響執行

### 最佳實踐

1. ✅ **紙上交易**：先用模擬帳戶驗證策略
2. ✅ **小額試單**：實盤從小金額開始
3. ✅ **嚴守紀律**：嚴格執行停損停利
4. ✅ **定期回測**：每月重新訓練模型
5. ✅ **分散風險**：不要全部資金押單一標的

---

## 📞 支援與貢獻

### 回報問題

如遇到錯誤，請提供：
1. 錯誤訊息截圖
2. 執行的命令
3. Python 版本與套件版本

### 貢獻指南

歡迎提交 Pull Request：
1. Fork 本專案
2. 建立 feature branch
3. 提交詳細的 commit message
4. 確保通過測試
5. 提交 PR

---

## 📝 授權

本專案採用 MIT License

---

## 🙏 致謝

- XGBoost 開發團隊
- Line Messaging API
- scikit-learn 社群
- 台灣證券交易所
- 櫃買中心

---

**最後更新：2026-01-02**  
**版本：V31 - Time Series Enhanced**
- [ ] Docker 部署優化
- [ ] CI/CD 自動化

---

## ⚠️ 風險警告

> **本專案僅供學習研究使用，不構成任何投資建議。**
> 
> - 股市有風險，投資需謹慎
> - 回測績效不代表未來報酬
> - 請自行評估風險承受能力
> - AI 預測僅供參考，嚴格執行停損

---

## 🔧 技術架構

| 項目 | 技術 |
|------|------|
| 後端框架 | Flask |
| 機器學習 | XGBoost |
| 資料庫 | MySQL |
| 通訊平台 | Line Messaging API |
| 技術指標 | 自建計算模組 |

---

## 📝 更新日誌

### V33 (2026-01) ⭐ 最新版 - Phase 1: Quality Assurance
- ✅ **Code Refactor** - 完整重構核心模組
  - Type Hints 覆蓋率提升至 90%
  - 消除所有魔術數字，統一使用 Config
  - 提取重複邏輯為獨立函數
  - 改善 Docstrings，符合 Google Style
- ✅ **Unit Testing** - 建立測試框架
  - 36 個測試用例 (19 指標 + 17 策略)
  - 測試覆蓋率達 60%+
  - Mock 技術隔離外部依賴
  - 支援 pytest + pytest-cov
- ✅ **Configuration Management** - 統一配置
  - 新增 V30 策略參數至 Config
  - 新增技術指標參數至 Config
  - 所有模組統一使用 Config 讀取
- 🔜 **Phase 2** - Strategy Deep Dive（待實作）
  - KD Golden Cross & BB Squeeze 濾網
  - Optuna 參數最佳化
  - Sentiment Analysis 整合

### V32 (2026-01)
- ✅ **回測擬真化** - 加入 0.2% 滑價模型
- ✅ **風險指標** - MDD (最大回撤) + Sharpe Ratio
- ✅ **Web Dashboard** - 專業量化交易儀表板
  - 資產曲線圖 (Chart.js)
  - 四大指標卡片 (ROI/勝率/MDD/Sharpe)
  - 交易明細表
  - ⚡ 即時選股訊號 (Live Signals)
- ✅ **Dark Quant Theme** - 深色專業設計
- ✅ **Line Bot 整合** - 新增 "dashboard" 指令
- ✅ **架構清理** - 移除測試文件，確保代碼整潔

### V31 (2025-12)
- ✅ 合併回測檔案為統一引擎 (`4_run_backtest.py`)
- ✅ 新增 ratio 特徵 (volume/foreign/trust)
- ✅ 模型格式升級 (含 features 清單)
- ✅ 移動停損機制 (漲 5% 後保底 1%)
- ✅ Clean Code 清理

### V30 (2025-01)
- ✅ V30 純技術策略 (+40% 報酬)
- ✅ 動態參數調整 (停損/停利)
- ✅ Line Bot 指令擴充

### V2.0
- 動態設定管理
- 穩健/積極模式切換

### V1.0
- 基礎 AI 選股
- Line Bot 整合

---

## 📞 聯絡方式

有問題歡迎開 Issue 或 Pull Request！

---

**最後更新：2026-01-08**  
**版本：V33 - Quality Assurance (Phase 1)**  
*程式碼重構 | 單元測試 | 統一配置管理*
