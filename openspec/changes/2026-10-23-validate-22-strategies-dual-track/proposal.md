# 2026-10-23 22 Strategies Dual-Track Validation（22 個策略雙軌驗證）

## Why
22 個策略家族已完成工程驗證，但全部是 DATA_BLOCKED，有效回測與有效 OOS 都是 0（`docs/native22_validation_report.md`）。上一輪 `2026-10-23-recover-official-data-and-retraining-spec` 補齊了 2026-07-29 之後的官方資料，並抽樣對帳 MySQL，確認 MySQL 有整日錯誤（法人、價格、成交量），也把 7 個 AI 模型的重新訓練規格寫成草案。

目前距離可信回測還缺：

1. **認證過的歷史資料**：Stage 2 的 1,872 次請求、除權息、下市、財報與營收的可得時間。
2. **AI 路線**：模型必須重新訓練，訓練前還有 6 項程式修改。
3. **非 AI 路線**：15 個家族的工程已通過，但從未在認證資料上回測，也從未做過統計檢定。

本輪要建立一套驗證體系，能分開判斷三件事：「策略程式正確」、「資料可信」、「投資績效有效」。

## What Changes

**共同資料認證層（Phase 1）**
- 執行已核准的 Stage 2：T86 914 次、MI_MARGN 958 次，以季批次進行，並記錄每次請求的放行時間。
- 小量補件：TWT49U 2022 年與 2026-07-28 之後各 1 次範圍請求、2026-10-23 重新請求、下市清單 1 次。
- 逐項認證：價格、成交量與交易日；法人與融資；財報（MOPS PIT v3，用本機原始快取抽樣重新解析）；月營收；上市股票池與下市；除權息與現金股利；交易成本與成交限制。
- 官方資料與 MySQL 分開評等。

**Track A：7 個 AI 策略（Phase 2）**
- 實作規格第 10 節的 6 項修改：
  1. `dealer_consec_days` 依該股歷史計算；
  2. z-score 母體改為完整案例的全市場；
  3. 只納入 TWSE 上市代號；
  4. 模型契約讀取 `training_data_end` 與規格雜湊；
  5. recovered 對帶有規格雜湊的模型取消「chip_score 訓練口徑未證實」旗標；
  6. 新的訓練資料建構器與訓練函式，必須有 Owner 簽核檔才能執行。
- 用測試證明訓練資料與 recovered 每日選股逐值相同。
- 不訓練模型；7 個舊模型與結果保持不動。

**Track B：15 個非 AI 策略（Phase 3）**
- G2/G3（3）、D5（1）、Day3（11）使用既有參數與既有引擎，改用認證過的官方輸入重跑。
- 逐項檢查公式、可得時間與前視偏差；量化 A191_132 近似實作與標準公式的差異。
- 既有 384 組研究設定與所有身分、雜湊都保留。

**回測與統計驗證（Phase 4）**
- 只對資料合格的策略，在各家族既有的開發期界線內執行，結果標記為 DEVELOPMENT。
- 共用的事後成交稽核：下一交易日可成交價、漲跌停鎖死、停牌、下市、流動性占比。
- 報告成本、總報酬（純現金股利事件可計算）、同期 TAIEX 價格指數與報酬指數、市場環境分段、收益集中度、bootstrap 信賴區間，以及依試驗次數調整的 Deflated Sharpe。

**AI 與非 AI 比較（Phase 5）**
- 7 個原生策略「只用規則」的開發期回測，在資料合格的範圍內進行。
- 「規則 ＋ AI 排名」與正式比較只提出計畫，等新模型與獨立樣本具備後才執行。

**交付（Phase 6）**
- 22 個策略的狀態矩陣、資料品質報告、下載與對帳結果、Track A 準備狀態、15 個家族的個別結果、回測資格與阻擋原因、比較計畫、manifest 與測試結果。

## Non-goals
- 不訓練 AI 模型；不寫入 MySQL；不切換 recovered 正式模式；不修改策略參數、權重或風控門檻。
- 不覆寫封存結果與 Frozen OOS 紀錄；不把開發期或已讀過的資料標記為 OOS；不統一或降低各家族的 OOS 門檻。
- 不 push；Broker 維持 DISABLED。
- 先前發現的正式狀態檔被改寫，仍保留為完整性例外；Git 歷史中的 MySQL 明文密碼仍列為待管理者處理（P-1）。

## Capabilities
### New Capabilities
- `dual-track-validation`：共同資料認證層、Track A 訓練與推論一致性、Track B 認證資料重跑、共用成交稽核與統計檢定、22 策略狀態矩陣。

## Impact
- **修改**（只影響 recovered 與研究路徑，legacy 正式路徑不變）：
  - `core/calc_indicators.py`：`dealer_consec_days`；
  - `core/research/native_frame.py`：z-score 母體、上市代號、訓練資料建構；
  - `core/research/native_models.py`：契約欄位；
  - `jobs/run_daily.py`：recovered 旗標；
  - `core/backtest/runner.py`：研究契約下的 `rules_only` 選項，用於「只用規則」回測。
- **新增**：`scripts/dual_track_validation.py`、`core/research/model_training.py`（必須有簽核檔才能執行）、測試、報告、`outputs/dual_track_validation/<日期>/`。
- **重用**：`scripts/official_data_recovery.py`（下載、續傳、預算、對帳）；`fundamental_strategy_validation`（`simulate_target_weights`、`run_cost_tracks`、`moving_block_bootstrap`）；D5 `vectorbt_adapter`；Day3 `strategy_search`；`config/settings.py` 的 `FEE_RATE`、`TAX_RATE`、`SLIPPAGE_RATE`。
- **外部請求**：約 1,880 次（Stage 2 的 1,872 次加小量補件），上限 1,900 次，每次間隔 ≥ 3.2 秒，約 1.7 小時，背景分批執行。
