# Stock AI Line Bot V33

> 🔥 **V33 Phase 1+ 完成** | ATR 動態停損 + 大盤熔斷  
> ⚔️ **PK System 人機對決** | 模擬交易與 AI 績效比較  
> 🛡️ **回測績效** | 總報酬率 27.4% | 勝率 46.3%  
> 📅 **最後更新**: 2026-01-22

---

## 📊 專案簡介

整合 AI 機器學習與技術分析的台股選股系統，透過 Line Bot 提供即時選股推薦與個股診斷。

### 🎯 核心功能

| 功能 | 說明 | 命令 |
|------|------|------|
| 🔥 V31 混合策略 | V30 篩選 + XGBoost 排名 | 輸入「推薦」 |
| 🚀 V30 純技術策略 | 均線突破 + 量能確認 + 大盤熔斷 | 輸入「V30」 |
| 🎫 個股診斷 | 完整策略報告 + ATR 動態停損 | 輸入股票代號 |
| 📊 Web Dashboard | 視覺化回測績效與即時選股 | `http://localhost:5000/dashboard` |

---

## 🏗️ 系統架構 (V33)

```
┌─────────────────────────────────────────────────────────┐
│                   🌐 應用層 (Application)                │
│   Line Bot (app.py) │ Web Dashboard │ 本地測試           │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   📊 策略層 (Strategy)                   │
│   tool/strategy.py     │  tool/news_agent.py            │
│   (V30/V31 選股邏輯)    │  (情緒分析)                     │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   📈 分析層 (Indicators)                 │
│   tool/calc_indicators.py                                │
│   (MA, RSI, MACD, KD, BB, ATR)                           │
└────────────┬────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────┐
│                   🗄️ 資料層 (Data)                       │
│   1_update_database.py (爬蟲) → MySQL (Docker)           │
│   XGBoost Model (.pkl) │ 回測結果 (.csv)                 │
└─────────────────────────────────────────────────────────┘
```

---

## 🛡️ V33 策略強化特性

### 風險控制機制

| 機制 | 說明 | 設定 |
|------|------|------|
| 🔥 大盤熔斷 | 收盤 < MA60 時禁止買入 | `USE_MARKET_FILTER = True` |
| 📈 趨勢濾網 | 個股收盤 > MA60 | `USE_TREND_FILTER = True` |
| 🛡️ ATR 動態停損 | 波動大則寬，波動小則窄 | `USE_ATR_STOP = True` |

### ATR 停損計算

```python
# 停損價格 = 收盤價 - (ATR × 乘數)
stop_loss = close - (atr * Config.ATR_MULTIPLIER)  # 預設乘數 2.0
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

# 4. 設定環境變數 (複製 .env.example 為 .env)
# 必要設定：DB_URL, LINE_TOKEN, LINE_SECRET

# 5. 初始化資料庫
python init_settings.py
```

### 每日更新流程

```powershell
# 一鍵整合腳本（推薦）
python 2_rundaily.py

# 或手動分步執行
python 1_update_database.py                           # 爬取股價
python -c "from tool.calc_indicators import main; main()"  # 計算指標
python 5_push_to_line.py                              # Line 推播
```

### 回測與訓練

```powershell
# 執行回測
python 4_run_backtest.py

# 重新訓練模型
python 3_train_model.py
```

---

## 📁 目錄結構

```
Stock_Linbotv1/
├── 📊 每日流程腳本
│   ├── 1_update_database.py     # 爬取股價 + 籌碼
│   ├── 2_rundaily.py            # 整合腳本
│   └── 5_push_to_line.py        # Line 推播
│
├── 🤖 AI 模型與回測
│   ├── 3_train_model.py         # XGBoost 訓練
│   ├── 4_run_backtest.py        # 統一回測引擎
│   └── 5_optimize_params.py     # Optuna 參數優化
│
├── 🌐 使用者介面
│   ├── app.py                   # Flask + Line Bot
│   ├── debug_local.py           # 本地互動測試
│   └── templates/               # Web Dashboard
│
├── ⚙️ 核心模組 (tool/)
│   ├── strategy.py              # V30/V31 選股邏輯
│   ├── calc_indicators.py       # 技術指標 (含 ATR)
│   ├── db_helper.py             # 資料庫操作
│   └── news_agent.py            # 情緒分析
│
├── 📦 數據與模型
│   └── ML_Data/                 # 模型 + 回測結果
│
├── 📄 設定檔
│   ├── config.py                # 統一設定中心
│   ├── requirements.txt         # Python 依賴
│   └── docker-compose.yaml      # Docker 部署
│
└── 📝 文檔
    ├── README.md                # 本文件
    ├── UpdateList.md            # 版本更新記錄
    └── openspec/                # 開發規範
```

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

**版本**: V33 Phase 1+ (2026-01-22)  
**授權**: MIT License
