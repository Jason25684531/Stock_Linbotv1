# 📋 Stock Linbot V1 更新日誌

---

## 🚀 V32 版本更新 (2026-01-06)

### Phase 1: 回測擬真化 (Backtest Realism) ✅

#### 1. **滑價模型實作** 
- **檔案**: `config.py`
  - 新增 `SLIPPAGE_RATE = 0.002` (0.2% 滑價率)
  - 新增 `RISK_FREE_RATE = 0.01` (年化無風險利率 1%)

- **檔案**: `4_run_backtest.py`
  - **買入邏輯**: 實際成本 = 市價 × (1 + 0.2%)，模擬買在更高價
  - **賣出邏輯**: 實際收入 = 市價 × (1 - 0.2%)，模擬賣在更低價
  - 更真實反映市場摩擦成本

#### 2. **風險指標計算**
- **最大回撤 (MDD - Max Drawdown)**
  - 計算公式: `max((peak - trough) / peak)`
  - 反映策略在回測期間的最大資產虧損幅度
  
- **夏普比率 (Sharpe Ratio)**
  - 計算公式: `(年化報酬 - 無風險利率) / 年化波動度`
  - 衡量每單位風險的報酬率
  - 使用 252 個交易日進行年化

#### 3. **數據輸出優化**
- **新增檔案**: `ML_Data/backtest_profit_report.csv`
  - 包含每日資產價值與報酬率
  - 用於 Phase 2 Dashboard 的資產曲線圖表
  - 欄位: `date`, `asset_value`, `roi`

- **更新檔案**: `ML_Data/backtest_result.csv`
  - 保留原有交易明細
  - 買入/賣出價格已反映滑價影響
  - 欄位: `stock_id`, `buy_date`, `sell_date`, `buy_price`, `sell_price`, `profit_pct`, `reason`, `days`

#### 4. **回測結果改善**
- **V32 擬真版測試結果** (2025-01-02 ~ 2026-01-05):
  ```
  📊 交易次數: 64
  🎯 勝率: 43.8%
  📊 盈虧比: 1.74
  ⏱️ 平均持有: 8.0 天
  📈 報酬率: +19.61%
  📉 最大回撤 (MDD): 61.57%
  📊 夏普比率: 0.812
  💸 滑價成本: 0.2% (買高賣低)
  ```

- **對比 V31 原版**: 
  - 滑價模型使報酬率更保守、更貼近實盤
  - 新增 MDD 與 Sharpe 提供全面的風險評估

---

## 📊 架構改善

### 程式碼品質提升
1. **統一配置管理**: 所有交易參數集中在 `config.py`
2. **保持模組化**: 不影響原有 V30/V31 雙模式運作
3. **易於擴展**: 為 Phase 2 Dashboard 提供標準化數據介面

### 技術債處理
- 保留原有階梯式移動停損邏輯 (Level 1/2/3)
- 代碼註釋清晰標示 V32 新增功能
- CSV 輸出格式標準化，便於前端讀取

---

## 🔜 下一階段 (Phase 2 & 3)

### Phase 2: Web Dashboard Infrastructure ✅
- [x] 在 `app.py` 新增 `/dashboard` 路由
- [x] 建立 `templates/base.html` (TailwindCSS + Alpine.js)
- [x] 建立 `templates/dashboard.html`
- [x] 建立 API 端點 `/api/performance` 提供 JSON 數據
- [x] 建立 API 端點 `/api/trades` 提供交易明細
- [x] 建立 API 端點 `/api/summary` 提供總結數據

### Phase 3: Data Visualization ✅
- [x] 使用 Chart.js 繪製資產曲線圖
- [x] 實作每日選股列表表格 (Alpine.js)
- [x] 整合 MDD/Sharpe 指標顯示

---

## 🎨 V32 Dashboard 實作細節 (2026-01-06)

### Backend API 實作

#### 1. **Flask 路由新增** (`app.py`)
```python
@app.route("/") 
@app.route("/dashboard")
- 主 Dashboard 頁面，渲染 dashboard.html

@app.route("/api/performance")
- 回傳資產曲線數據 (dates, equity, roi)
- 資料來源: ML_Data/backtest_profit_report.csv

@app.route("/api/trades")
- 回傳交易明細（最近 50 筆）
- 資料來源: ML_Data/backtest_result.csv

@app.route("/api/summary")
- 回傳總結指標 (total_roi, win_rate, mdd, sharpe, trade_count, avg_hold_days)
- 動態計算 MDD 與 Sharpe Ratio
```

### Frontend 設計

#### 2. **設計美學** (遵循 `frontend-design.md`)
- **配色方案**: 深色量化主題 (Dark Quant Theme)
  - 主背景: `#0a0e1a` (深藍黑)
  - 卡片背景: `#1a2132` (深灰藍)
  - 強調色: 綠色 `#10b981` (獲利) / 紅色 `#ef4444` (虧損)
  
- **字體選擇**: JetBrains Mono (等寬字體，適合數字顯示)
  - 避免 Inter/Roboto 等 AI 常用字體
  - 專業量化交易風格

- **動畫效果**:
  - 背景漸變動畫 (gradient-shift)
  - 卡片懸停效果 (hover transform + glow)
  - Loading spinner

#### 3. **功能模組**

**A. 指標卡片 (Stats Grid)**
- 4 個核心指標：總報酬率、勝率、最大回撤、夏普比率
- 根據數值動態變色 (正值綠色、負值紅色)
- 懸停時發光效果

**B. 資產曲線圖 (Equity Curve)**
- 使用 Chart.js 繪製
- 根據最終 ROI 決定線條顏色 (獲利綠色/虧損紅色)
- Area fill 增強視覺效果
- Tooltip 顯示資產價值與 ROI
- X 軸每 20 天顯示一個日期標籤

**C. 交易明細表 (Recent Trades)**
- 顯示最近 20 筆交易
- 賣出原因以色彩標示：
  - 停損: 紅色背景
  - 停利: 綠色背景
  - 時間到/趨勢轉空: 灰色背景
- 懸停時背景變色

#### 4. **技術架構**
- **前端框架**: Alpine.js (輕量級響應式框架)
- **CSS 框架**: TailwindCSS CDN (無需 npm)
- **圖表庫**: Chart.js CDN
- **字體**: Google Fonts (JetBrains Mono)
- **資料流**: Fetch API → Alpine.js State → DOM 渲染

### 測試結果

#### 啟動成功 ✅
```bash
Flask 伺服器運行於: http://127.0.0.1:5000
Dashboard 路由: http://localhost:5000/dashboard
API 端點測試: 
  - /api/performance ✅
  - /api/trades ✅
  - /api/summary ✅
```

#### Dashboard 功能驗證
- [x] 資產曲線圖正常顯示
- [x] 四大指標卡片動態更新
- [x] 交易明細表正確顯示
- [x] 響應式設計適配手機/平板
- [x] 深色主題專業美觀

---

## 📊 V32 完整架構圖

```
┌─────────────────────────────────────────────┐
│        Stock Linbot V32 Architecture        │
└─────────────────────────────────────────────┘

┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Phase 1    │───▶│   Phase 2    │───▶│   Phase 3    │
│ 回測擬真化   │    │ Web Backend  │    │ 視覺化儀表板 │
└──────────────┘    └──────────────┘    └──────────────┘
       │                   │                    │
       ▼                   ▼                    ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ 滑價模型    │    │ Flask 路由   │    │ Chart.js    │
│ MDD 計算    │    │ 3 個 API     │    │ Alpine.js   │
│ Sharpe 計算 │    │ Jinja2 模板  │    │ TailwindCSS │
└─────────────┘    └─────────────┘    └─────────────┘
       │                   │                    │
       ▼                   ▼                    ▼
┌──────────────────────────────────────────────┐
│        CSV 數據層 (ML_Data/)                  │
│  • backtest_result.csv (交易明細)            │
│  • backtest_profit_report.csv (資產曲線)     │
└──────────────────────────────────────────────┘
```

---

## 📝 備註

- **測試環境**: Windows + Python 3.10+ + myenv 虛擬環境
- **資料庫**: MySQL 8.0 (Docker)
- **回測期間**: 2025-01-02 ~ 2026-01-05 (245 個交易日)
- **前端方針**: 使用 CDN，避免 npm/webpack 等 Node.js 依賴

---

## ⚡ V32 Phase 4: System Integration (2026-01-06)

### 實作內容

#### 1. **即時選股訊號 API** (`app.py`)

新增 `GET /api/daily-signals` 端點：
- 自動讀取最新資料庫數據
- 呼叫 `get_best_stocks_v31_hybrid()` 執行 V31 混合策略選股
- 回傳 JSON 格式的選股結果

**回應格式**:
```json
{
  "date": "2026-01-06",
  "signals": [
    {
      "stock_id": "2330",
      "close_price": 580.0,
      "strategy": "V31 混合策略",
      "ai_score": 0.85,
      "rsi": 55.2,
      "volume": 45000000,
      "ma20": 575.5,
      "foreign_buy": 12000000
    }
  ],
  "count": 5
}
```

#### 2. **Dashboard 即時訊號區塊** (`dashboard.html`)

新增 **⚡ Live Signals** 區域：
- 卡片式設計，每檔股票獨立顯示
- 資訊包含：
  - 股票代號 (藍色大字)
  - 收盤價
  - AI Score (信心度評分，顏色分級)
  - RSI 指標
  - 成交量 (K 為單位)
  - MA20 均線

- **視覺設計**:
  - 左側藍色邊框強調
  - Grid 響應式布局 (1/2/3 欄自適應)
  - AI Score 依照分數變色：
    - ≥ 70%: 綠色 (高信心)
    - ≥ 50%: 琥珀色 (中等)
    - < 50%: 灰色 (低信心)

- **空狀態處理**:
  - 顯示提示圖標與說明文字
  - 引導用戶執行資料更新腳本

#### 3. **Line Bot 整合** (`app.py`)

新增指令：
- **"dashboard"** / **"儀表板"** → 回覆 Dashboard URL 連結
- 提供功能說明：
  - 資產曲線圖
  - 回測績效指標
  - 交易明細表
  - 即時選股訊號

更新說明選單，將 Dashboard 功能列為 "V32 新功能"

#### 4. **系統整合測試**

測試流程：
```bash
1. 執行回測: python 4_run_backtest.py --v31
2. 啟動伺服器: python app.py
3. 訪問 Dashboard: http://localhost:5000/dashboard
4. 檢查 Live Signals 區塊是否正常顯示
5. 測試 Line Bot "dashboard" 指令
```

測試結果：
- ✅ `/api/daily-signals` API 正常回應
- ✅ Dashboard Live Signals 區塊正常渲染
- ✅ 卡片式布局響應式設計良好
- ✅ AI Score 顏色分級正確
- ✅ Line Bot "dashboard" 指令正常回覆
- ✅ 空狀態提示正常顯示

---

## 🎯 V32 完整流程架構 (Final)

```
┌──────────────────────────────────────────────────────────────┐
│                  Stock Linbot V32 Complete                   │
└──────────────────────────────────────────────────────────────┘

┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Phase 1   │───▶│   Phase 2   │───▶│   Phase 3   │───▶│   Phase 4   │
│ 回測擬真化  │    │ Web Backend │    │ 視覺化儀表板│    │ 系統整合    │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
       │                   │                    │                  │
       ▼                   ▼                    ▼                  ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ • 滑價 0.2% │    │ Flask 路由   │    │ Chart.js    │    │ Live Signals│
│ • MDD 計算  │    │ 4 個 API     │    │ 資產曲線    │    │ API 整合    │
│ • Sharpe    │    │ Jinja2 模板  │    │ 交易明細表  │    │ Line Bot    │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
       │                   │                    │                  │
       └───────────────────┴────────────────────┴──────────────────┘
                                  ▼
                    ┌──────────────────────────┐
                    │   Daily Operation Flow    │
                    └──────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │  1_update_database.py     │ (爬蟲)
                    │  ↓                        │
                    │  tool/calc_indicators.py  │ (特徵工程)
                    │  ↓                        │
                    │  /api/daily-signals       │ (選股訊號)
                    │  ↓                        │
                    │  Dashboard Live Signals   │ (視覺化)
                    │  ↓                        │
                    │  5_push_to_line.py        │ (Line 推播)
                    └───────────────────────────┘
```

### 完整功能清單

| 模組 | 功能 | 狀態 |
|------|------|------|
| **Phase 1** | 滑價模型 (0.2%) | ✅ |
| | MDD 計算 | ✅ |
| | Sharpe Ratio 計算 | ✅ |
| | 每日資產曲線輸出 | ✅ |
| **Phase 2** | Flask Dashboard 路由 | ✅ |
| | `/api/performance` | ✅ |
| | `/api/trades` | ✅ |
| | `/api/summary` | ✅ |
| | `/api/daily-signals` | ✅ |
| **Phase 3** | 資產曲線圖 (Chart.js) | ✅ |
| | 四大指標卡片 | ✅ |
| | 交易明細表 | ✅ |
| | 響應式設計 | ✅ |
| **Phase 4** | 即時選股訊號區塊 | ✅ |
| | Line Bot Dashboard 指令 | ✅ |
| | 系統整合測試 | ✅ |

---

## 🚀 使用方式

### 日常操作流程

1. **每日更新資料** (早上 9:00 盤後)
   ```bash
   python 2_rundaily.py
   ```
   自動執行：爬蟲 → 特徵計算 → Line 推播

2. **執行回測** (策略調整後)
   ```bash
   python 4_run_backtest.py --v31
   ```

3. **查看 Dashboard** (隨時)
   ```bash
   python app.py
   # 訪問 http://localhost:5000/dashboard
   ```

4. **Line Bot 使用**
   - 輸入 `V30` → 純技術分析選股
   - 輸入 `推薦` → V31 混合策略選股
   - 輸入 `dashboard` → 取得儀表板連結
   - 輸入 `2330` → 個股診斷

---

## 📊 績效指標 (V32 擬真版)

**回測期間**: 2025-01-02 ~ 2026-01-05 (245 交易日)

| 指標 | 數值 | 說明 |
|------|------|------|
| 總報酬率 | +19.61% | 含滑價成本的真實報酬 |
| 勝率 | 43.8% | 獲利交易佔比 |
| 盈虧比 | 1.74 | 平均獲利 / 平均虧損 |
| 交易次數 | 64 筆 | 平均 3.8 天一筆交易 |
| 平均持有 | 8.0 天 | 符合短線策略定位 |
| 最大回撤 (MDD) | 61.57% | **需優化** |
| 夏普比率 | 0.812 | 風險調整後報酬 |
| 滑價成本 | 0.2% | 買高賣低的真實成本 |

---

## 📝 備註

- **測試環境**: Windows + Python 3.10+ + myenv 虛擬環境
- **資料庫**: MySQL 8.0 (Docker)
- **回測期間**: 2025-01-02 ~ 2026-01-05 (245 個交易日)
- **前端方針**: 使用 CDN，避免 npm/webpack 等 Node.js 依賴
- **Line Bot**: 支援 V30/V31 選股、個股診斷、Dashboard 連結

---

## 🧹 V32 架構清理與優化 (2026-01-06)

### 清理項目

#### 1. **移除測試文件**
- ✅ 刪除 `static/2330.png` (測試用股票圖片)
- ✅ 刪除 `static/2603.png` (測試用股票圖片)
- **原因**: 這些圖片未被任何程式碼引用，保留會造成混淆

#### 2. **架構驗證**
已確認專案符合 Clean Architecture 原則：

```
📱 Presentation Layer (應用層)
  ├─ app.py (Flask 路由 + Line Bot Webhook)
  │  ├─ GET / 和 /dashboard (Dashboard 主頁)
  │  ├─ GET /api/performance (資產曲線數據)
  │  ├─ GET /api/trades (交易明細)
  │  ├─ GET /api/summary (總結指標)
  │  └─ GET /api/daily-signals (即時選股)
  └─ debug_local.py (本地測試工具)

📊 Business Logic Layer (業務層)
  ├─ tool/strategy.py (V30/V31 策略核心邏輯)
  │  ├─ get_v30_candidates() - V30 均線突破篩選
  │  ├─ get_best_stocks_v31_hybrid() - V31 混合策略
  │  ├─ format_v30_recommendation() - V30 格式化
  │  ├─ format_v31_recommendation() - V31 格式化
  │  └─ format_stock_query() - 個股診斷格式化
  │
  ├─ tool/calc_indicators.py (技術指標計算)
  │  └─ 計算 MA, RSI, MACD, KD, BB 等指標
  │
  └─ tool/news_agent.py (新聞 AI 分析)
      └─ Gemini API 整合

🗄️ Data Access Layer (數據層)
  └─ tool/db_helper.py (資料庫 CRUD 封裝)
      ├─ get_stock_data() - 讀取股價數據
      ├─ get_setting() - 讀取設定
      ├─ update_setting() - 更新設定
      └─ validate_setting() - 驗證設定

⚙️ Configuration Layer (配置層)
  └─ config.py (統一設定管理)
      ├─ 資料庫連線字串
      ├─ AI 模型路徑與特徵
      ├─ V32 交易參數 (SLIPPAGE_RATE, RISK_FREE_RATE)
      └─ Line Bot & Gemini API Keys

🔄 Workflow Scripts (工作流腳本)
  ├─ 1_update_database.py (證交所/櫃買中心爬蟲)
  ├─ 2_rundaily.py (每日自動化：爬蟲→特徵→推播)
  ├─ 3_train_model.py (XGBoost 模型訓練)
  ├─ 4_run_backtest.py (V30/V31 回測引擎)
  └─ 5_push_to_line.py (Line 推播通知)

🌐 Frontend (前端)
  ├─ templates/base.html (Dark Quant Theme 基礎模板)
  └─ templates/dashboard.html (Dashboard 主頁面)
      ├─ Chart.js 資產曲線圖
      ├─ Alpine.js 響應式資料綁定
      ├─ TailwindCSS 深色量化風格
      └─ 四大指標卡片 + 交易明細表 + Live Signals
```

#### 3. **代碼品質評估**

| 評估項目 | 狀態 | 說明 |
|---------|------|------|
| 關注點分離 | ✅ 優秀 | app.py 純路由，業務邏輯在 tool/ |
| 重複代碼 | ✅ 良好 | 無重複定義，模組化清晰 |
| 設定管理 | ✅ 優秀 | 統一使用 Config 類別 |
| 文檔完整性 | ✅ 優秀 | README + UpdateList 詳細記錄 |
| 測試文件 | ✅ 已清理 | 移除 static/ 測試圖片 |
| 可擴展性 | ✅ 優秀 | 易於新增策略或 API |

#### 4. **依賴關係圖**

```
config.py (核心配置)
    ↓
    ├→ 1_update_database.py (爬蟲)
    │      ↓
    ├→ tool/calc_indicators.py (特徵工程)
    │      ↓
    ├→ 3_train_model.py (訓練模型)
    │      ↓
    ├→ tool/strategy.py (策略邏輯)
    │      ↓
    └→ app.py (Web API + Line Bot)
           ↓
       templates/dashboard.html (前端視覺化)
```

### 優化建議

#### 短期 (已完成)
- ✅ 移除未使用的測試圖片
- ✅ 驗證模組間依賴清晰無循環
- ✅ 確認所有 API 端點正常運作

#### 中期 (建議執行)
- 🔄 新增單元測試 (pytest)
- 🔄 加入 Docker Compose 完整部署
- 🔄 監控 MDD 61.57% 過高問題，優化風控

#### 長期 (規劃中)
- 💡 實盤交易對接 (模擬券商 API)
- 💡 多策略並行回測比較
- 💡 AutoML 自動特徵選擇

---

*最後更新: 2026-01-06 by V32 Complete + Architecture Cleanup*
