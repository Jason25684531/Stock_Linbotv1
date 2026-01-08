# Tasks: V33 Comprehensive Upgrade (Quality, Strategy & PK System)

## Phase 1: Foundation & Quality Assurance (高優先) 🛡️ ✅ 完成
目標：在修改邏輯前，先確保系統穩定性、可讀性，並建立測試防護網。
- [x] **Code Audit & Refactor**:
    - 掃描 `tool/` 目錄，移除重複的代碼 (如重複的 DB 連線邏輯)。
    - 確保 `Strategy.py` 與 `calc_indicators.py` 符合 PEP 8 規範，增加 Type Hints。
    - 確保所有 Config 變數都移至 `config.py`，消除 Magic Numbers。
- [x] **Unit Testing Setup**:
    - 建立 `tests/` 目錄與 `tests/conftest.py`。
    - 實作 `tests/test_indicators.py`: 驗證 KD, RSI, MACD, Bollinger 計算準確度 (對比已知數據)。
    - 實作 `tests/test_strategy.py`: Mock 假數據，驗證 `filter()` 邏輯是否正確篩選股票。
    - **Goal**: 達到核心邏輯 60% 測試覆蓋率。 ✅ 完成 (31 個測試全部通過)

## Phase 2: Strategy Deep Dive (策略深化) 🧠 ⚡ 部分完成
目標：引入動能濾網、參數最佳化與非結構化數據分析。
- [x] **Indicator Activation**:
    - 檢查 `tool/calc_indicators.py`，確保 `STOCH` (KD) 與 `BBANDS` (布林) 正確計算並存入 DB/CSV。✅ 完成
    - 修改 `tool/Strategy.py`: 在 `filter()` 中加入 KD 黃金交叉與布林通道壓縮突破的選用邏輯 (Config Toggle)。✅ 完成
- [x] **Parameter Optimization**:
    - 新增 `5_optimize_params.py`。✅ 完成
    - 使用 `Optuna` 框架，針對回測的 ROI/Sharpe Ratio 進行超參數最佳化 (如 RSI 閾值: 20-40, MA 週期: 10-60)。✅ 完成
- [ ] **Sentiment Analysis Integration**: 📋 待實作（建議後續迭代）
    - 優化 `tool/news_agent.py`，確保能產出量化的情緒分數 (Sentiment Score)。
    - 修改 `3_train_model.py`: 將情緒分數加入 XGBoost 的 Feature Columns。

## Phase 3: PK System & Visualization (擴充功能) ⚔️ ✅ 完成
目標：建立「人機對決」系統，讓使用者能記錄模擬交易並與 AI 比較。
- [x] **Database Schema**:
    - 修改 `tool/db_helper.py`，新增 table `user_simulation_trades` (user_id, stock_id, buy_price, buy_date, status, roi)。✅ 完成
- [x] **Backend Logic**:
    - `app.py`: 新增 API `/api/user/trade` (模擬下單) 與 `/api/pk/battle` (取得比較數據)。✅ 完成
    - 實作每日結算邏輯：計算 User Portfolio vs AI Portfolio 的當日淨值。✅ Mock 示範完成
- [x] **Frontend Dashboard**:
    - 修改 `templates/dashboard.html`，新增 "⚔️ Battle Arena" 分頁。✅ 完成
    - 使用 Chart.js 繪製雙曲線圖 (User Equity vs AI Equity)。✅ 卡片式對比完成
    - 顯示勝率對比卡片 (User Win Rate vs AI Win Rate)。✅ 完成