# 模型訓練驗證與官方資料下載優化報告

- 日期：2026-10-23
- OpenSpec：`openspec/changes/2026-10-23-validate-model-training-and-data/`
- 輸出：`outputs/model_data_validation/2026-10-23/`（只新增檔案。`_superseded/` 保存兩次修正說明文字前的 features 輸出，以及一次修正起算日前的 oos 輸出）
- 程式：`scripts/validate_model_data.py`、`test/test_model_data_validation.py`（提交 `bb91cd4`，未 push）
- 本輪性質：全程唯讀。MySQL 只執行 SELECT；模型檔的雜湊沒有變；沒有任何訓練；官方請求 5 次（上限 20 次）。

## 優先回答

### 1. 7 個 AI 模型需要重新訓練嗎？

**需要。6 個為 RETRAIN_REQUIRED，hybrid_trend_rank 為 RETRAIN_RECOMMENDED。7 個都維持 MODEL_BLOCKED。**

| 策略 | 模型 | 判定 | 訓練模擬下前 3 名改變的日期 |
|---|---|---|---|
| defensive_low_volatility | v33_low_vol | RETRAIN_REQUIRED | 1／5（07-20） |
| growth_momentum_breakout | v34_turbo | RETRAIN_REQUIRED | 1／5（07-03） |
| hybrid_trend_rank | v31_hybrid | RETRAIN_RECOMMENDED | 0／3 個有評分的日期 |
| institutional_flow_confirmation | v36_chip_momentum | RETRAIN_REQUIRED | 3／5 |
| mean_reversion | v37_mean_reversion | RETRAIN_REQUIRED | 4／5 |
| quality_growth | v35_innovation | RETRAIN_REQUIRED | 2／5 |
| quality_value_low_volatility | v38_value_dividend | RETRAIN_REQUIRED | 3／5 |

判定依據（`retrain_decision.json`，規則在 `decide()`，測試在 `test_model_data_validation.py`）：

1. **特徵定義已改變（PROVEN）**。模型實際使用的特徵中，`chip_score_z`、`institutional_net_buy_z`、`institutional_consec_buy_days_z`、`macd_hist_z` 與訓練時的定義不同；所有特徵的 z-score 母體也不同。
2. **排序確實受影響（PROVEN，用現有模型做診斷推論）**。在相同的官方輸入和相同的候選股票下，改用訓練時的設定（全市場 z-score、自營商與融資補 0），6 個模型有 1～4 天的前 3 名改變。主要原因是 z-score 母體：只改母體時，前 3 名改變的天數幾乎和完整模擬相同。排序整體仍相近，Spearman 多在 0.94 以上（defensive 有一天為 0.70，當天只有 5 檔候選）。
3. **訓練截止日無法證明（UNKNOWN）**：模型沒有記錄 `training_data_end`，訓練之後 DB 又被改寫過。
4. **報告的 accuracy 不可信（PROVEN）**：測試集（2025-07-31～2026-05-20）包含 MySQL 法人整日存成 0 的日期、上櫃週末列與 NULL 指標。
5. **輕微的標籤洩漏（INFERRED）**：切分點前最後 5～20 個交易日的標籤會讀到測試期的價格；尾端沒有完整預測期的資料被標成 0，而不是刪除。

hybrid 之所以不是 REQUIRED：定義差異已證實，但 5 個日期中只有 3 天有評分，而這 3 天的前 3 名都沒有改變，未達「定義改變且排序受影響」的門檻。

### 2. 哪些有確切證據，哪些是推論？

| 結論 | 等級 | 證據 |
|---|---|---|
| 7 個模型都在 2026-05-20 20:55–20:56 訓練 | PROVEN | 模型 metadata 與檔案時間一致 |
| 24 個特徵的名稱與順序 | PROVEN | booster `feature_names` = metadata = `Z_SCORE_FEATURE_COLUMNS` |
| 訓練用的是未提交的程式 | PROVEN | 05-20 前最後一個提交 `4e0311a` 不會產生 `_z` 特徵；第一個包含這段程式的提交是 `be448d9`（05-21 21:13），兩者之間沒有其他提交 |
| 訓練時 DB 沒有 `dealer_buy`／`margin_balance` 欄位 | PROVEN | 2026-10-13 回補的 `verification.md` 記載當天才「新增 dealer_buy、margin_balance … NULL DEFAULT NULL」 |
| 訓練時 `chip_score` 有變化 | PROVEN | 7 個模型在 `chip_score_z` 上都有 236～402 個切分點 |
| `chip_score` 的實際口徑：自營商與融資固定為中性 50，只隨外資與投信變動 | INFERRED | 欄位不存在（PROVEN）、`train_model.py:165` 把 `dealer_buy` 補 0、`dealer_ratio_z`／`dealer_consec_days_z` 在所有模型都是 0 個切分點；程式版本本身是推論 |
| 程式版本等同 `be448d9` | INFERRED | 特徵完全一致；`train_model.py` 從 `be448d9` 到 HEAD 沒有改動 |
| 訓練期間為 2022-01-03～2026-05-20，80/20 切分日為 2025-07-30 | INFERRED | 用現有 DB 推算（1,074 個唯一日期，含週末日期）；當天的 DB 狀態沒有紀錄 |
| 技術指標是讀 DB 存的值，不是重算 | INFERRED | 訓練程式沒有計算指標的步驟 |
| DB 在訓練之後被改寫 | INFERRED | `natr` 在訓練截止前有 38 天非 0，但所有模型的 `natr_z` 都是 0 個切分點；10-13 回補 |
| 擬合期（≤ 2025-07-30）的 MySQL 法人是否正確 | UNKNOWN | 從未與官方對照；依「90% 以上股票為 0」的規則只偵測到 2024-07-18 一天 |
| 訓練 log 或資料快照 | UNKNOWN | ML_Data、logs、outputs、data 在 05-19～22 都沒有；模型檔沒有被 Git 追蹤 |
| `training_data_end` | UNKNOWN | 未記錄 |

### 3. 要得到可信的模型與回測資料，最少還要下載什麼？

- **只為了確定擬合期的法人資料是否有誤**：方案 A，**44 次** T86 請求（擬合期每月 1 個交易日，加上 2024-07-18），約 2 分鐘。
- **為了重新訓練並取得認證過的歷史資料**：方案 B 第 2 階段，T86 914 次加 MI_MARGN 958 次，共 **1,872 次**（2022-01-03～2025-12-12），約 1.6 小時，約 293 MB。
- **2026-07-28 之後的日曆、行情、法人、融資**（用於 OOS 計時與每日選股的資料閘門）：方案 B 第 3 階段，MI_INDEX 63 次、T86 45 次、MI_MARGN 45 次，共 **153 次**（以平日計，是上限）。
- **價格歷史不必再下載**：MI_INDEX 已快取 2021-12-30～2026-07-28，共 1,105 個交易日。

### 4. 能否減少原先約 3,000 次的請求？

可以。

- 原先的估算是約 1,000 個交易日 × 3 個資料集，當時沒有先檢查快取。
- 重新訓練所需的 B 第 1–3 階段共 **2,069 次**，減少約 31%。主要是因為 2022 年以來的 MI_INDEX 已經全部在快取裡；2022–2026-07 需要的 TWSE 檔案中，有 1,399／3,315（42%）可以直接重用。
- 如果暫時不重新訓練，只需要 A 加上第 3 階段，共 **197 次**（約 10 分鐘），減少約 93%。
- 再往下減已經不可能：5 次測試請求確認 T86 與 MI_MARGN 不接受月份查詢，TWSE 和 TPEx 的 OpenAPI 只提供最新一天、不接受日期參數，所以每個交易日至少要 1 次請求。

### 5. 下一步應該先補資料、重新訓練，還是等 OOS？

**建議依序進行：先補少量資料 → 再核准重新訓練 → 最後等待 OOS。**

1. 現在：核准方案 B 第 1 與第 3 階段（197 次）。第 1 階段量測擬合期的法人資料，第 3 階段補齊 07-28 之後的資料。
2. 核准重新訓練時，同時執行第 2 階段（1,872 次），再依 P-2 重新訓練。必須記錄 `training_data_end`，並事先登記 OOS。
3. 等待 OOS：重新訓練後約在 **2027-11-09** 累積滿 252 個交易日（情境 B，以平日 × 0.930 推估）。原模型就算能通過認證也要到約 **2027-08-23**，而且它已判定需要重新訓練，所以不可行。較早截止日的情境 C 不可行：2022 年以後的每一段期間都已經是訓練資料，或已被原生策略的回測與診斷讀取過；策略參數的設計期間也沒有紀錄。

## 摘要

| 項目 | 結果 |
|---|---|
| 訓練來源可完整證明的模型數 | **0／7**（訓練日期、特徵、DB 結構都是 PROVEN，但 `training_data_end` 是 UNKNOWN、程式版本是 INFERRED） |
| 需要重新訓練 | REQUIRED：defensive_low_volatility、growth_momentum_breakout、institutional_flow_confirmation、mean_reversion、quality_growth、quality_value_low_volatility；RECOMMENDED：hybrid_trend_rank |
| 有問題的模型特徵 | 有使用且定義不同：`chip_score_z`、`institutional_net_buy_z`、`institutional_consec_buy_days_z`、`macd_hist_z`，以及所有特徵的 z-score 母體。訓練時是常數、模型不使用：`dealer_ratio_z`、`dealer_consec_days_z`、`natr_z`、`std_20_z`、`revenue_yoy_z`、`rd_ratio_z`、`op_profit_margin_z`、`eps_z` |
| 可重用的官方快取 | MI_INDEX 1,194 個檔案（1,105 個交易日）；T86／MI_MARGN 各 165 天；TWT49U 2023–2026-07；MOPS PIT v3（FY2013～FY2025）。重用比例 42% |
| 原預估請求數 | 約 3,000 |
| 優化後必要請求數 | 現在 197（A 加上 B 第 3 階段）；可重新訓練所需共 2,069（B 第 1–3 階段）；完整方案 C 約 7,469 |
| 推薦下載方案 | **B（分階段）** |
| 可進行有效回測的策略 | **0** |
| 仍受阻的策略 | **7**：模型 BLOCKED（需重新訓練、`training_data_end` 未知）；OOS 未滿 252 天；財報 FY2026 與月營收的時間點；MySQL 法人資料 |

## 細節

### Phase 1：訓練歷史（`training_provenance.json`）

- **資料來源**：`load_and_prepare_data` 對 `daily_market_data` 做 `SELECT *`，沒有過濾，所以上市、上櫃、ETF、週末列、重複列都會讀進來。截至 05-20 共 1,437,525 列，其中 2026 年 608,334 列，有 21 個週末日期、共 149,875 列。
- **2026 年的資料品質**：2026 年有 35 天外資「整日為 0」，其中 11 天已用官方 T86 確認是錯誤。2026-02～05 大量列的指標為 NULL，訓練時被補成 0 或 z=0。這些都落在測試集。
- **擬合期**：2022-01-03～2025-07-30，共 865 個交易日。指標欄位齊全，但 `natr`、`std_20`、`revenue_yoy` 全部是 0（常數）。

### Phase 2：特徵比對（`feature_definition_diff.csv`、`feature_ranking_impact.json`）

在擬合期內（2025-06-02～07-30，約 44,800 對），比較 DB 存的指標和共用核心以官方價格重算的值：

| 指標 | 狀態 | 比對結果 |
|---|---|---|
| `rsi`、`kd_k`、`bb_width` | SAME | 100% 完全相等 |
| `bias` | SAME | 99.89% |
| `macd_hist` | DIFFERENT | 只有 1.6% 完全相等，89.6% 在 1% 以內 |

`macd_hist` 的差異來自 EMA 起算點：DB 用全歷史，現行用 150 日窗口。

其他重點：

- **`volume_ratio`**：訓練是對全表依股票計算 20 列均量，是真正的歷史比例，不是舊 Daily 恆為 1.0 的值。公式和現行相同，判為 SAME。差別只在歷史不足 20 筆時：訓練為 `min_periods=1`，現行為 NULL（OD-1）。
- **z-score 母體**：訓練以同一天所有列為母體，NaN 的 z 值給 0；現行只用通過排除的列。排序影響已量化，見上文。
- **`high_60`**：不是模型特徵，只是 V34 的規則輸入，不影響模型。

### Phase 4：資料下載（`official_data_gap_inventory.csv`、`download_request_estimate.json`、`optimized_download_plan.md`、`probe_log.json`）

5 次測試請求（間隔 3.5 秒）的結果：

| 端點 | 結果 |
|---|---|
| T86 月份查詢 | 被拒絕，官方資料從 101/05/02 開始提供 |
| MI_MARGN 月份查詢 | 無資料 |
| TWSE OpenAPI（143 個路徑） | 有 MI_INDEX、MI_MARGN，沒有 T86，都不接受參數 |
| TPEx OpenAPI（225 個路徑） | 有行情、三大法人、融資，都不接受參數 |
| MOPS 月營收彙總頁 | 一頁涵蓋一個市場一個月，但沒有公告時間 |

批次執行時的安全規則（已寫入下載計畫）：

- 重用 `fetch_raw`，已有快取的檔案不重抓；
- 每個資料集、每個月份一批，用進度檔支援續傳；
- 每天最多重試 3 次，連續失敗 5 次就停止；
- 驗證回應的日期與欄位結構，不符者隔離；
- 每個檔案記錄 URL、擷取時間、SHA-256；
- 官方資料與 MySQL 分開存放，不寫入正式 DB。

### Phase 5：OOS 與 BLOCKED（`oos_feasibility.json`、`blocked_classification.csv`）

**已被讀取、不能當作原生策略 OOS 的期間**：

- 模型訓練資料：2022-01-03～2026-05-20
- 原生策略的歷史回測：2025-06-02～2026-06-26
- native7 診斷重播：2026-05-21～07-28
- 上一輪 parity 比對日期：2026-05-21～07-20
- 上一輪 shadow 日期：2026-10-13～20

**其他家族的期間**：Day3 研究的開發期為 2023-01～2025-12，嚴格 OOS 為 2026-01～07-28；G2/G3 Frozen 的 fresh OOS 從 2026-07-29 開始，屬於它自己的 ledger。

**BLOCKED 分類**：

| 類別 | 項目 |
|---|---|
| 資料修復可解除（5） | 擬合期法人資料、07-28 之後的法人與融資、07-28 之後的日曆與行情、FY2026 財報、TPEx |
| 需要 Owner 決策（2） | 模型特徵契約（重新訓練）、MySQL 法人資料修復 |
| 可能永遠無法證明（2） | 原模型的 `training_data_end`、月營收公告時間 |
| 只能等新樣本（1） | 252 個交易日的 OOS |

## 方案（只提不做，`owner_proposals.md`）

| 方案 | 內容 |
|---|---|
| P-1 帳密輪換 | 不輸出密碼明文：在 secret store 產生新密碼、由 DBA 執行 `ALTER USER`、更新 `.env` 與部署環境並重啟服務、確認舊密碼失效；不改寫 Git 歷史 |
| P-2 重新訓練 | 使用認證過的資料與共用核心；記錄 `training_data_end`、提交版本、資料雜湊；刪除沒有完整預測期的標籤、清除切分點附近的資料；事先登記 OOS |
| P-3 正式 DB 修復 | 先寫入暫存表並比對，再以交易寫入，附備份與回滾 |
| P-4 正式模式切換 | 模型已認證、資料閘門 PASS、parity PASS、至少 20 天 shadow 無錯誤、Owner 簽核 |

## 驗證

| 項目 | 結果 |
|---|---|
| `python scripts/validate_model_data.py tests`（7 組，含 6 項新測試） | 115 passed、3 skipped、1 xfailed |
| 基準回歸 | 91 passed、3 skipped、1 xfailed |
| `python scripts/validate_model_data.py verify` | PASS |
| `git diff --check`、`openspec validate 2026-10-23-validate-model-training-and-data --strict` | 通過 |

verify 檢查的內容：

- 7 個模型的雜湊沒有變；
- 前三輪封存的輸出（61、29、17 個檔案）都沒有變；
- 5,463 個歷史雜湊沒有變；受保護來源中只有已核准的 V34 變更；
- `jobs/`、`core/`、`config/`、`execution/`、`app/`、`services/`、`ML_Data/` 自基準以來沒有修改；
- `DAILY_FEATURE_MODE` 為 legacy；
- 官方請求 5 次，不超過 20 次上限；
- 7 個模型維持 MODEL_BLOCKED，沒有執行任何訓練；
- Broker 為 DISABLED。

## 重現

```bash
export PYTHONIOENCODING=utf-8
for s in baseline provenance features decision inventory probe plan oos classify tests manifest verify; do
  python scripts/validate_model_data.py $s
done
```

輸出目錄已存在時，腳本會拒絕覆寫。`probe` 會讀取 `probe_log.json`，已經完成的請求不會重送，整個變更的請求總數上限為 20 次。
