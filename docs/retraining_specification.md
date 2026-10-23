# 7 個原生策略模型：重新訓練規格（草案，待 Owner 簽核）

- 日期：2026-10-23
- 規格版本：`retraining-spec-v1`，狀態 **DRAFT_PENDING_OWNER_APPROVAL**
- 機器可讀版本：`outputs/official_data_recovery/2026-10-23/retraining_spec.json`
- 規格 SHA-256（不含 `sha256` 欄位本身的 JSON，key 排序）：`acfc4d76e517f88177cd9413d239bbba3845052b3530bfe983c4fc35fa512288`
- 本文件由程式從現有程式碼與模型 metadata 產生數值（特徵清單、各策略的預測天數與目標報酬、切分日期），之後再由人說明。本輪沒有訓練任何模型，也沒有修改策略參數。

## 0. 如何固定這份規格

1. Owner 審閱本文件與 JSON，逐項回答第 11 節的決策。
2. 簽核後，簽核記錄必須引用上面的 SHA-256。
3. 簽核之後，任何門檻、特徵、切分比例或超參數的修改都會改變雜湊，因此是新版本（`retraining-spec-v2`），必須重新簽核。`test_changing_a_threshold_changes_the_specification_hash` 驗證這一點。
4. 每個新模型檔都會記錄這個雜湊，推論時比對；雜湊不符就不載入。
5. 看到任何訓練結果之後，不得修改標準；要改就是新版本，而且該結果所使用的 Validation／Test 區段視為已被讀過。

## 1. 原則

- **訓練與推論用同一條規則**：一列資料只有在該模型的特徵全部有值時，才能進入訓練、Validation、Test 與推論。缺值不補 0、不補平均、不補最近一次的值（「完整案例」）。
- **訓練特徵 = 每日選股實際使用的特徵**：以 `NativeResearchData.cross_section` 逐交易日計算（每個交易日用截至該日的 150 個日曆日窗口，與 recovered 每日選股相同的 `build_feature_history`）。這比對整個歷史一次算完慢，約需 1 小時，（估計約 1 小時，尚未實測），但可以讓訓練與推論的 `macd_hist` 等指標完全一致；上一輪的比對顯示，若分開計算，`macd_hist` 只有 1.6% 的資料完全相同。
- **只改訓練方式，不改策略**：預測天數與目標報酬沿用現有模型的 metadata；策略參數、權重、風控門檻不動。

## 2. 股票池與價格

- 股票池：上市（TWSE）四位數普通股，來自 MI_INDEX 收盤行情表。**上櫃不納入**：沒有經認證的上櫃官方來源。這表示 recovered 每日選股必須在推論時只保留上市代號（目前會讀到 MySQL 的上櫃列，見第 10 節）。
- 價格：原始價（價格報酬）。除權息日只旗標，不還原。標籤的預測期若包含除權息日，原始價會讓標籤偏保守（只可能少標成 1），報告時列出受影響的比例。
- 交易日：官方 MI_INDEX 判定的交易日；沒有官方回應的日期不使用。

## 3. 24 個特徵（17 個進入模型，7 個排除）

欄位順序以 `feature_order` 為準，名稱都加 `_z`。

| # | 特徵 | 進入模型 | 公式 | 來源 | 缺值規則 / 排除原因 |
|---|---|:---:|---|---|---|
| 1 | rsi | 是 | `calculate_rsi(close)` | MI_INDEX 收盤 | 窗口未滿為缺值 |
| 2 | bias | 是 | `(close − ma20) / ma20 × 100` | MI_INDEX 收盤 | ma20 缺值或 0 為缺值 |
| 3 | macd_hist | 是 | `calculate_macd(close)` | MI_INDEX 收盤 | 窗口未滿為缺值 |
| 4 | kd_k | 是 | `calculate_kd(high, low, close)` | MI_INDEX 高低收 | 窗口未滿為缺值 |
| 5 | bb_width | 是 | `calculate_bb_width(close)` | MI_INDEX 收盤 | 窗口未滿為缺值 |
| 6 | volume_ratio | 是 | `volume（0 換成 1）/ 最近 20 筆成交量均值`，限制在 0～5 | MI_INDEX 成交量 | 有效成交量不足 20 筆為缺值（OD-1） |
| 7 | natr | 是 | `calculate_natr` | MI_INDEX 高低收 | 窗口未滿為缺值 |
| 8 | std_20 | 是 | `calculate_std_20(close)` | MI_INDEX 收盤 | 窗口未滿為缺值 |
| 9 | foreign_ratio | 是 | `foreign_buy / volume`，限制 ±0.5 | T86 外陸資（不含外資自營商）／MI_INDEX 成交量 | 不在 T86 為缺值 |
| 10 | trust_ratio | 是 | `trust_buy / volume`，限制 ±0.5 | T86 投信 | 不在 T86 為缺值 |
| 11 | dealer_ratio | 是 | `dealer_buy / volume`，限制 ±0.5 | T86 自營商 | 不在 T86 為缺值 |
| 12 | foreign_consec_days | 是 | 該股歷史中連續 `foreign_buy > 0` 的交易日數 | T86 | 當日外資缺值為缺值 |
| 13 | trust_consec_days | 是 | 同上，投信 | T86 | 同上 |
| 14 | dealer_consec_days | 是 | 同上，自營商 | T86 | 同上；**須先修程式（第 10 節）** |
| 15 | institutional_net_buy | 是 | `foreign_buy + trust_buy + dealer_buy` | T86 | 任一分量缺值即缺值 |
| 16 | institutional_consec_buy_days | 是 | 外資＋投信＋自營商的連買天數；淨買超 ≤ 0 時為 0 | T86 | 任一分量缺值即缺值；**須先修程式** |
| 17 | chip_score | 是 | `0.4 f + 0.3 t + 0.15 d + 0.15 m`，0～100（權重為 `Config.CHIP_WEIGHT_*`） | T86 + MI_MARGN | 任一分量缺值即缺值，**不得補中性值 50** |
| 18 | large_holder_ratio | 否 | 大戶持股比例 | 沒有任何來源 | 沒有經認證的來源 |
| 19 | large_holder_ratio_change | 否 | 大戶持股比例變化 | 沒有任何來源 | 同上 |
| 20 | news_sentiment_score | 否 | 每個交易日一個市場情緒代碼 | 新聞模組（沒有歷史紀錄） | 同一天所有股票的值相同，z-score 為 0，沒有橫截面資訊，也沒有歷史 |
| 21 | revenue_yoy | 否 | 月營收年增率 | MySQL monthly_revenue | 沒有官方公告時間，只有次月 10 日的估計（OD-12） |
| 22 | rd_ratio | 否 | `rd_expense / revenue` | MOPS PIT v3 | 實際公告日只到 FY2025，FY2026 為截止日估計 |
| 23 | op_profit_margin | 否 | `operating_profit / revenue` | MOPS PIT v3 | 同上 |
| 24 | eps | 否 | 單季 EPS | MOPS PIT v3 | 同上 |

說明：舊模型中 `dealer_ratio_z`、`dealer_consec_days_z`、`natr_z`、`std_20_z` 與所有財報類特徵的重要性是 0，因為訓練時它們是常數。新規格把前四項補上真實值，並將無法證明公告時間的財報與營收類特徵移出模型。**Owner 若想把財報類特徵放進模型，需要先取得 FY2026 的公告時間（約 1,780 次請求的估計，見資料缺口），並重新簽核**。

## 4. z-score 母體

- 同一交易日、通過完整案例的所有上市普通股，計算 `(x − 平均) / 標準差`（母體標準差，標準差為 0 則 z = 0）。
- 母體**不隨策略規則排除而變動**。目前 recovered 每日選股與研究是在策略篩選之後才算 z-score，與舊訓練的「同一天所有列」不同；上一輪實測這會改變 1～4 個日期的前 3 名。新規格統一為「完整案例的全市場」，推論端必須配合修改（第 10 節）。
- 缺值的列不在母體內，也不會被補值。

## 5. 缺值與資料不足

- 完整案例規則（第 1 節）。
- 該股上市未滿窗口長度、停牌、不在 T86、不在 MI_MARGN（非融資標的）、第一個交易日（沒有前一日融資餘額）都會造成缺值而被排除。上一輪粗估，約有 3% 的股票日因沒有融資資料而排除。
- 每次訓練與推論都要輸出被排除列的數量與原因。

## 6. 標籤與預測期

沿用現有定義與各策略的參數：

> 標籤 = 1，若 `max(未來 N 個官方交易日的最高價) / 當日收盤 − 1 > 目標報酬`，否則為 0。

| 策略 | 模型名稱 | N（交易日） | 目標報酬 |
|---|---|---:|---:|
| defensive_low_volatility | v33_low_vol | 10 | 5% |
| growth_momentum_breakout | v34_turbo | 5 | 10% |
| hybrid_trend_rank | v31_hybrid | 7 | 8% |
| institutional_flow_confirmation | v36_chip_momentum | 10 | 7% |
| mean_reversion | v37_mean_reversion | 8 | 5% |
| quality_growth | v35_innovation | 20 | 15% |
| quality_value_low_volatility | v38_value_dividend | 15 | 5% |

與舊程式的兩點差異（都是修正錯誤）：

1. 沒有完整 N 個交易日預測期的列直接刪除。舊程式把它們的報酬補成 0，標成 0。
2. 預測期以官方交易日計算，不使用週末列或重複列。

## 7. 時間切分與洩漏防護

- 以官方交易日數切分：Train 60%、Validation 20%、Test 20%。起點是第一個特徵齊全的交易日（約 2022-02-22）。
- 每個切分點的前面扣掉 N 個交易日（purge，N 是該模型的預測天數），後面再扣掉 5 個交易日（embargo）。這樣訓練列的標籤預測期一定在下一個區段開始之前結束。`test_no_label_horizon_crosses_a_split_boundary` 對 N = 5、7、8、10、15、20 驗證：區段互不重疊，訓練最後一列的預測期結束日早於 Validation 的第一天，兩段之間正好隔 N + 5 個交易日。
- Test 區段最後 N 個交易日的標籤因為預測期超出資料終點而被刪除。
- 下表是「資料終點為最後一個已認證交易日（目前是 2026-10-22）」時的**示意**邊界。正式邊界在資料 manifest 固定時才確定。

| 策略 | N | Train | Validation | Test | 最後有標籤的 Test 日 |
|---|---:|---|---|---|---|
| defensive_low_volatility | 10 | 2022-02-22～2024-11-22（671） | 2024-12-16～2025-10-30（212） | 2025-11-21～2026-10-22（222） | 2026-10-07 |
| growth_momentum_breakout | 5 | ～2024-11-29（676） | 2024-12-16～2025-11-06（217） | 同上 | 2026-10-15 |
| hybrid_trend_rank | 7 | ～2024-11-27（674） | ～2025-11-04（215） | 同上 | 2026-10-13 |
| institutional_flow_confirmation | 10 | ～2024-11-22（671） | ～2025-10-30（212） | 同上 | 2026-10-07 |
| mean_reversion | 8 | ～2024-11-26（673） | ～2025-11-03（214） | 同上 | 2026-10-12 |
| quality_growth | 20 | ～2024-11-08（661） | ～2025-10-15（202） | 同上 | 2026-09-21 |
| quality_value_low_volatility | 15 | ～2024-11-15（666） | ～2025-10-22（207） | 同上 | 2026-09-30 |

括號是交易日數。

**提醒**：Test 區段（2025-11-21 起）與先前已被讀取的期間（native7 診斷重播 2026-05-21～07-28、上一輪的 parity 與 shadow 日期）重疊。這些讀取是針對舊模型與診斷，沒有用來調整新模型，所以不影響新模型在 Test 上的驗收；但因此 **Test 不是 OOS，也不能作為投資績效的證據**（第 9 節）。

## 8. 模型與超參數

超參數沿用 `jobs/train_model.py`，不調參：

| 參數 | 值 |
|---|---|
| 模型 | XGBClassifier |
| n_estimators | 300 |
| learning_rate | 0.03 |
| max_depth | 5 |
| min_child_weight | 3 |
| subsample / colsample_bytree | 0.7 / 0.7 |
| scale_pos_weight | `(負樣本 / 正樣本) × 0.5`，只用訓練區段計算 |
| random_state | 42 |
| 調參與提早停止 | 無。Validation 只用於驗收，不用於調整 |

## 9. 預先登記的驗收門檻與 OOS 規範

**門檻是提案值，需 Owner 確認。** 簽核之後固定。

### 9.1 驗收（Validation 與 Test 都必須通過；Test 只評估一次）

母體：該區段的完整案例列（全部上市普通股）。每個指標先在每個交易日計算，再對區段取平均。

| 指標 | 門檻 | 定義 |
|---|---:|---|
| 前 10% 提升度（平均） | ≥ 1.20 | 每天以分數排序，前 10% 中標籤為 1 的比例 ÷ 當天的基準比例 |
| 提升度為正的月份比例 | ≥ 60% | 平均提升度大於 1.0 的日曆月佔全部月份的比例 |
| 整體 ROC AUC | ≥ 0.55 | 區段內分數對標籤 |
| 平均排序相關（Rank IC） | ≥ 0.02 | 每天 Spearman（分數，實現的未來最高漲幅） |

護欄（全部通過才算合格）：

1. 每個訓練列的標籤預測期在下一個區段開始之前結束（purge 檢查）。
2. 刪除某日之後的所有資料，該日的特徵值不變（對未來資料獨立）。
3. 把標籤打亂之後的 AUC 落在 0.48～0.52。
4. 沒有任何交易日同時屬於兩個區段。
5. 模型的特徵清單等於本規格的 `feature_order`。
6. 資料 manifest 的 SHA-256 等於登記的雜湊。

失敗時：模型被拒絕，不得在同一份 Validation 或 Test 上調整後重評；要重做就是新規格版本。

### 9.2 OOS 規範

- OOS 從 `training_data_end` 的下一個交易日開始。
- 至少 252 個交易日（既有門檻，不降低）。只計算從未被任何回測、診斷或調參讀取的交易日。
- 指標與門檻使用 9.1 的同一組。累積滿 252 日後評估一次；期間可以監控，但不得根據結果重新訓練或修改門檻。
- 資料終點若是 2026-10-23，最早在約 **2027-11-09** 滿 252 個交易日（以平日 × 0.930 的交易日比例推估，實際日期依官方日曆）。
- Test 區段不是 OOS。

## 10. 推論契約，以及訓練之前必須先改的程式

推論時：

- 特徵由 `build_feature_history` 與 `build_multi_factor_matrix` 產生，名稱與順序等於模型記錄的特徵清單。
- 只保留上市四位數代號。
- z-score 母體是該交易日所有完整案例。
- 缺值的列排除，不補值；沒有有效模型就不產生正式排名。
- 分數 = `predict_proba[:, 1]`。
- 模型閘門：必須有 `training_data_end` 與規格雜湊，而且視窗起點在 `training_data_end` 之後。

**這些在現有程式中尚未成立，本輪不得修改 `core/`，所以列為訓練前的必要修改：**

| # | 位置 | 問題 | 修改 |
|---|---|---|---|
| 1 | `core/calc_indicators.py::build_feature_history` | **已用實測確認**：recovered 路徑的 `dealer_consec_days` 由單日橫截面計算，結果只可能是 0 或 1。測試資料中自營商連續 6 天買超，輸出卻是 1；`institutional_consec_buy_days` 也因此不正確。舊模型不受影響，因為訓練時這兩個特徵是常數 | 以該股歷史計算連買天數，自營商缺值則為缺值 |
| 2 | `build_multi_factor_matrix` 的呼叫端 | z-score 在策略規則篩選之後才計算 | 改為完整案例的全市場 |
| 3 | recovered 每日選股 | 會讀到 MySQL 的上櫃列 | 只保留上市代號 |
| 4 | `core/research/native_models.py` | 以 `training_date` 為視窗上界，沒有檢查規格雜湊 | 讀取 `training_data_end` 與規格雜湊 |
| 5 | `jobs/run_daily.py` recovered | 特徵 `chip_score_z` 一律標為訓練口徑未證實 | 對帶有規格雜湊的模型取消這個旗標 |
| 6 | 新的訓練腳本 | `jobs/train_model.py` 對 MySQL `SELECT *`、不過濾、並把沒有完整預測期的列標 0 | 以官方資料與本規格重寫（不是修改舊腳本） |

## 11. 需要 Owner 回答的決策

| ID | 問題 | 建議 |
|---|---|---|
| OD-14 | 模型特徵：17 個（價量與法人）還是加入財報類 | 先用 17 個；財報類等取得 FY2026 公告時間後再開新版本 |
| OD-15 | 股票池：只含上市，或納入上櫃 | 只含上市；上櫃另外認證 |
| OD-16 | 驗收門檻（第 9.1 節四項與護欄） | 同意提案值，或指定其他數值，在簽核前決定 |
| OD-17 | 切分比例 60／20／20，purge ＝ 預測天數，embargo ＝ 5 | 同意 |
| OD-18 | 超參數沿用現有值、不調參 | 同意 |
| OD-19 | 核准第 10 節 6 項程式修改（下一輪，在訓練之前） | 同意，並要求每項有測試 |
| OD-20 | 核准 Stage 2 下載（1,872 次，約 1.7 小時） | 同意（見報告第 5 節） |
| OD-21 | 資料終點：以 Stage 2 完成當時最後一個已認證交易日為準，並在訓練前登記 | 同意 |

## 12. 資料與版本記錄

- 資料 manifest：Stage 2 完成後，把 T86、MI_MARGN、MI_INDEX、PIT 財報（若使用）的每個檔案與 SHA-256 彙整成一份 manifest，計算整體雜湊，在訓練前登記。
- 模型版本字串：`<策略名稱>-TS2-<規格雜湊前 8 碼>`。
- 模型檔記錄：`training_data_start`、`training_data_end`、切分邊界、規格雜湊、資料 manifest 雜湊、程式 commit、特徵清單與順序、標籤參數、驗收指標。
- 舊的 7 個模型檔保持不動，新檔案並存，直到通過認證。
