# 22 策略雙軌驗證報告

變更：`openspec/changes/2026-10-23-validate-22-strategies-dual-track`（OD-22～OD-29）。證據目錄：`outputs/dual_track_validation/2026-10-23/`（下文簡稱 `OUT/`）。

**本輪結論**：22 個策略的程式都通過引擎檢查。共同的官方價格、法人、融資與交易日資料已經認證，但 MySQL 不合格。**沒有任何策略**取得申請獨立驗證的資格。7 個 AI 策略都需要重新訓練，現在只能做規則版診斷。15 個非 AI 家族中，有 7 個家族的部分設定達到 DEVELOPMENT 回測等級，但全部沒有通過統計門檻。所有結果都屬於開發期研究，不是 OOS 證明。

## 1. 22 策略狀態表

ENGINE／DATA／BACKTEST／EVIDENCE 四個維度分開評等。BACKTEST：DEVELOPMENT＝資料、計算與成交稽核都符合開發期規範；DIAGNOSTIC＝部分必要證據未通過；BLOCKED＝缺必要資料或無法可靠計算。Track B 以家族為單位：只要家族中有任何一組設定是 DEVELOPMENT，就列 DEVELOPMENT，括號內是組數。

| # | 策略 | 軌 | ENGINE | DATA | BACKTEST | EVIDENCE | 可申請獨立驗證 | 仍需解除的阻擋 |
|---|---|---|---|---|---|---|---|---|
| 1 | defensive_low_volatility | A | PASS | PASS | DIAGNOSTIC（規則版） | INSUFFICIENT | 否 | 重新訓練（MODEL_BLOCKED）；規則參數試驗次數不明；價格報酬基礎 |
| 2 | growth_momentum_breakout | A | PASS | **BLOCKED** | **BLOCKED** | INSUFFICIENT | 否 | 月營收公告時間；重新訓練 |
| 3 | hybrid_trend_rank | A | PASS | PASS | DIAGNOSTIC（規則版） | INSUFFICIENT | 否 | 同 1；沿用估值重大（3036 疑似暫停交易，占權益 48%） |
| 4 | institutional_flow_confirmation | A | PASS | PASS | DIAGNOSTIC（規則版） | INSUFFICIENT | 否 | 同 1；沿用估值重大（18%） |
| 5 | mean_reversion | A | PASS | PASS | DIAGNOSTIC（規則版） | INSUFFICIENT | 否 | 同 1；沿用估值重大、疑似暫停交易（32%） |
| 6 | quality_growth | A | PASS | **BLOCKED** | **BLOCKED** | INSUFFICIENT | 否 | 月營收公告時間；財報數值驗證；重新訓練 |
| 7 | quality_value_low_volatility | A | PASS | **BLOCKED**（財報數值未驗證） | DIAGNOSTIC（規則版，至 2026-05-14） | INSUFFICIENT | 否 | 財報數值驗證；重新訓練；沿用估值重大（30%） |
| 8 | G2_OPERATING_INCOME_YOY | B1 | PASS | **BLOCKED**（財報數值未驗證） | DIAGNOSTIC（0/12） | INSUFFICIENT | 否 | 財報數值全量驗證；總報酬；統計門檻 |
| 9 | G3_EPS_YOY | B1 | PASS | **BLOCKED** | DIAGNOSTIC（0/12） | INSUFFICIENT | 否 | 同上 |
| 10 | G2+G3 | B1 | PASS | **BLOCKED** | DIAGNOSTIC（0/12） | INSUFFICIENT | 否 | 同上（Frozen Candidate 另由 Fresh OOS ledger 累積，未受本輪影響） |
| 11 | D5（5 因子） | B2 | PASS | PASS | DEVELOPMENT（44/48） | INSUFFICIENT | 否 | 總報酬（權息混合事件）；統計門檻 |
| 12 | A101_003 | B3 | PASS | PASS | DIAGNOSTIC（0/12） | INSUFFICIENT | 否 | 成交稽核重大；持有非官方估值價；總報酬；統計門檻 |
| 13 | A191_088 | B3 | PASS | PASS | DIAGNOSTIC（0/12） | INSUFFICIENT | 否 | 同上 |
| 14 | A191_132 | B3 | PASS | PASS | DEVELOPMENT（2/12） | INSUFFICIENT | 否 | 同上；公式為 IMPLEMENTATION_APPROXIMATE |
| 15 | RS_004_NEAR_HIGH_120D | B3 | PASS | PASS | DEVELOPMENT（4/12） | INSUFFICIENT | 否 | 同上 |
| 16 | A101_003+A191_088 | B3 | PASS | PASS | DIAGNOSTIC（0/36） | INSUFFICIENT | 否 | 同上 |
| 17 | A101_003+A191_132 | B3 | PASS | PASS | DIAGNOSTIC（0/36） | INSUFFICIENT | 否 | 同上 |
| 18 | A191_088+A191_132 | B3 | PASS | PASS | DEVELOPMENT（6/36） | INSUFFICIENT | 否 | 同上 |
| 19 | RS_004+A101_003 | B3 | PASS | PASS | DIAGNOSTIC（0/36） | INSUFFICIENT | 否 | 同上 |
| 20 | RS_004+A191_088 | B3 | PASS | PASS | DEVELOPMENT（18/36） | INSUFFICIENT | 否 | 同上 |
| 21 | RS_004+A191_132 | B3 | PASS | PASS | DEVELOPMENT（30/36） | INSUFFICIENT | 否 | 同上 |
| 22 | RS_004+A101_003+A191_088+A191_132 | B3 | PASS | PASS | DEVELOPMENT（6/36） | INSUFFICIENT | 否 | 同上 |

來源：`OUT/strategy_status_matrix.csv`、`strategy_status_summary.json`。

### 五個問題的直接回答

1. **ENGINE_PASS**：22 個全部。
2. **必要資料 DATA_PASS**：16 個（Native7 的 1、3、4、5；D5；Day3 的 11 個家族）。**DATA_BLOCKED** 6 個：growth_momentum_breakout、quality_growth（缺月營收公告時間）、quality_value_low_volatility 與 G2/G3 三個家族（財報數值只做過抽樣，沒有全量驗證）。
3. **能做可信的 DEVELOPMENT 回測**：7 個 Track B 家族中的 110 組設定（D5 44 組；Day3 66 組，分布在 A191_132、RS_004 與 4 個組合家族）。Native7 規則版 5 個只能是 DIAGNOSTIC，G2/G3 36 組都是 DIAGNOSTIC。
4. **有資格申請獨立驗證**：**0 個**。384 組全部 `EVIDENCE = INSUFFICIENT`：每一組都卡在總報酬認證（TOTAL_RETURN_BLOCKED），而且沒有任何一組通過 bootstrap 下界與 DSR 門檻。
5. **仍然 BLOCKED 的**：growth_momentum_breakout、quality_growth（BACKTEST 與 DATA 都 BLOCKED）。解除條件：月營收的實際公告時間（不能用截止日推估）、財報數值全量驗證，並在 Owner 簽核規格後重新訓練。

## 2. 384 組執行清單

`OUT/run_list_384.csv`：G2/G3 36、D5 48、Day3 300，**384 組全部 DONE**（計時探測估計全部重跑少於 4 小時，所以依 OD-25 全部執行，沒有挑選）。重跑再現性：G2/G3 8/8 個 CSV 位元相同；Day3 11/11 相同（全部止於 2025-12-31，沒有讀取嚴格 OOS 期間）；D5 48 組中 46 組相同，另外 2 組與先前修復比較中已知會改變的 2 組相同。家族資料界線：G2/G3 止於 2026-07-28、D5 依原研究契約、Day3 止於 2025-12-31（`verification.json` 有逐家族檢查）。

| BACKTEST | 組數 |
|---|---|
| DEVELOPMENT | 110 |
| DIAGNOSTIC | 274 |

## 3. 官方資料覆蓋率與對帳

| 資料（2022-01-03～2026-10-22） | 官方來源 | 說明 |
|---|---|---|
| 交易日與 MI_INDEX 價格 | DATA_PASS（1,164 個交易日） | 2026-10-23 兩次查詢都無資料，視為非交易日 |
| T86 三大法人 | DATA_PASS（1,164/1,164，每日中位數 999 檔） | Stage 2 補齊 914 日 |
| MI_MARGN 融資 | DATA_PASS（1,164/1,164，中位數 983 檔） | Stage 2 補齊 958 日 |
| D3 還原價格 | DATA_PASS（801,084 列，0 筆不符） | 67 列為停牌日的填補估值價（沒有官方成交），另列並在持倉時降級 |
| 除權息事件 | DATA_PASS（調整因子） | 混合「權息」事件無法拆出現金股利，所以總報酬仍 BLOCKED |
| 下市清單 | 265 檔 | 本輪 384 組沒有在下市時仍持有的情形 |
| **MySQL** | **DATA_FAIL** | 外資 24,921/1,018,687 筆不符，其中 25 天整日錯誤；投信 7,782 筆不符；自營與融資約 100 萬筆 stock-day 為 NULL；12 筆收盤價為 0（官方 `--` 被寫成 0） |

官方請求：Stage 2 與補件共 1,876 次放行，在 1,900 次預算內；每日最多 3 次嘗試。**放行間隔有 1 處不合格**：MI_MARGN 2022-10-07 與 T86 2022-10-11 之間記錄的間隔是 2.934 秒（要求 3.2 秒，驗證門檻 3.1 秒）；其餘 1,874 個間隔中最小為 3.177 秒，其中 832 個介於 3.1～3.2 秒。限速器以單調時鐘睡到滿 3.2 秒，記錄的時間是等待結束後的系統時間，所以 3.1～3.2 秒的差距屬於記錄延遲；但 2.934 秒無法用這個原因解釋，當時也沒有系統時鐘調整紀錄，因此 `verification.json` 對這一項判定 **FAIL**，沒有放寬門檻（`verification.json`、`request_ledger.jsonl`）。來源：`OUT/data_gate_results.json`、`official_mysql_reconciliation.csv`。

## 4. 交易成本與成交可行性稽核

- 成本：手續費 0.1425%、證交稅 0.3%、滑價 0.1%，另跑 2 倍成本壓力。
- 成交稽核（OD-23）：以單股當日成交金額的 1%、2%、5% 檢查，並檢查漲跌停鎖死與無成交。**只做事後旗標，沒有模擬未成交或部分成交，所以不能宣稱嚴格可成交。**

| 家族 | 交易筆數 | 超過 1% | 超過 2% | 超過 5% | 無法成交（鎖死或無量） | 稽核結果 |
|---|---|---|---|---|---|---|
| D5 | 70,122 | 24 | 10 | 0 | 80 | 48 組 PASS_FLAG_ONLY |
| Day3 | 235,004 | 797 | 134 | 8 | 2,029（多為漲停鎖死的買單） | 174 組 PASS_FLAG_ONLY、**126 組 FLAGGED_MATERIAL** |
| G2/G3 | 23,940 | 1,596 | 525 | 76 | 34 | 36 組 PASS_FLAG_ONLY |

逐組明細：`OUT/execution_audit.csv`。

## 5. 回測統計與多重比較修正

- 證據序列：每日淨超額報酬（相對發行量加權股價報酬指數，含息；同期 Sharpe 1.683）。
- Bootstrap：moving block（區塊 20、2,000 次、種子 20261023），取年化 Sharpe 的百分位區間。
- DSR：Bailey 與 López de Prado 的方法，試驗次數 **743**（384 組設定＋335 個候選因子＋12＋12 的既有搜尋），這是已知搜尋的下限，所以實際的修正只會更嚴。
- 門檻（OD-24）與結果：

| 門檻 | 通過的組數（384 組中） |
|---|---|
| g1 2 倍成本下淨 Sharpe 與超額 Sharpe > 0 | 46（D5 29、Day3 17、G2/G3 0） |
| g2 bootstrap 下界 > 0 | **0**（最高 −0.257，D5） |
| g3 DSR > 0.95 | **0**（最高 0.030） |
| g4 前 5 檔與最佳 5% 交易日集中度 < 50% | 0（逐字解讀「最佳 5% 交易日占總報酬」時沒有任何一組通過；結論不受影響，因為 g2、g3 已全部失敗） |
| g5 多空頭不同時為負，且各期至少 60 個交易日 | 135（D5 48、Day3 75、G2/G3 12）；通過者也都是空頭超額為負、多頭為正，空頭期只有約 84 個交易日，列為風險 |

- 多因子對單因子（同參數配對、配對 bootstrap 的 Sharpe 差）：Day3 252 組中 171 組無法區分、81 組多因子較差、**0 組較好**；G2/G3 12 組全部無法區分。**目前沒有證據支持多因子較好。**

各家族中位數（`strategy_status_matrix.csv`）：

| 家族 | 淨 Sharpe 中位數 | 超額 Sharpe 中位數 | bootstrap 下界最大值 | DSR 最大值 |
|---|---|---|---|---|
| D5 | 1.398 | 0.169 | −0.257 | 0.0297 |
| A191_132 | 1.201 | 0.125 | −0.369 | 0.0245 |
| A191_088+A191_132 | 0.950 | −0.059 | −0.670 | 0.0252 |
| G2+G3 | 0.997 | −0.200 | −0.780 | 0.0072 |
| 其他 11 個家族 | 0.300～0.884 | −1.186～−0.244 | −1.615～−0.673 | ≤ 0.0190 |

## 6. Track A：六項修復與規則版結果

六項修復與測試對照（`OUT/track_a_readiness.json`、`test/test_track_a_fixes.py`）：

| 修復 | 位置 | 測試重點 |
|---|---|---|
| dealer_consec_days、institutional_consec_buy_days 依歷史計算 | `core/calc_indicators.py` | 連續天數正確；沒有法人資料時維持 NULL |
| z-score 母體＝17 個模型特徵完整的股票，規則篩選在後 | `core/research/native_frame.py` | 規則輸入不改變母體；缺模型特徵者不在母體；legacy 行為不變 |
| 只納入上市普通股 | `native_frame.listed_universe`、`jobs/run_daily.py` | 只讀官方 MI_INDEX 快取；不可用時明確失敗 |
| 模型契約讀取 training_data_end 與 spec_sha256 | `core/research/native_models.py` | 期間界線與規格雜湊檢查；舊模型照舊載入 |
| 訓練框架與核准閘門 | `core/research/model_training.py` | 沒有核准就拒絕，且不讀任何資料；訓練框架與 recovered 逐值相同；標籤只用之後的交易日 |
| 規則版回測 | `core/backtest/runner.py` `rules_only=True` | 不需模型，只依規則輸入排除股票 |

規格 `retraining-spec-v1` 維持 `DRAFT_PENDING_OWNER_APPROVAL`（SHA-256 `acfc4d76…`）。7 個舊模型維持 `MODEL_BLOCKED`，本輪沒有任何訓練。

規則版回測（OD-29 修復後，2023-01-03～2026-05-14，原始價格，基準為股價指數，同期 +193.5%）：

| 策略 | 總報酬 | Sharpe | MDD | 超額 Sharpe | 沿用估值 |
|---|---|---|---|---|---|
| defensive_low_volatility | −21.09% | −0.541 | −31.73% | −2.005 | 0 次 |
| mean_reversion | −33.55% | −0.620 | −42.22% | −2.048 | 6 次，最高 32% |
| hybrid_trend_rank | −69.64% | −1.308 | −69.65% | −2.549 | 1 次，48% |
| institutional_flow_confirmation | −12.14% | −0.337 | −26.51% | −1.944 | 2 次，18% |
| quality_value_low_volatility | −35.47% | −1.081 | −37.33% | −2.368 | 5 次，30% |

詳細內容：`OUT/native7_nan_root_cause_report.md`、`OUT/native7_valuation_fix_report.md`。這些規則版結果都不能用來評估 AI 的價值；它們使用原始價格，也不能與 Track B 的含息結果並排比較（`OUT/comparison_plan.md`）。

## 7. Track B：公式檢查

`OUT/track_b_formula_checks.json`：Day3 的 4 個因子公式與獨立參考實作逐值相同（rtol 1e-9）；其中 A101_003 的生產程式把非有限值的相關係數視為缺值，參考實作採用相同規則，並記錄筆數。D5 的 5 個因子與所需歷史長度一致。G2/G3 的 PIT 檢查通過。A191_132 仍標為 IMPLEMENTATION_APPROXIMATE：官方成交金額與「典型價 × 量」兩種定義的排名 Spearman 0.99999、前 10 名重疊 99.8%，差異很小。

## 8. 完整性

見 `OUT/integrity_report.md`。重點：模型、封存與 Frozen ledger 都不變；沒有新增雜湊例外；**正式狀態檔在 2026-10-24 03:57 被本輪的回歸測試（`test_run_daily_persistence.py`）改寫成 asof 2026-04-28 的測試版本**，而歷史雜湊記錄的版本本身也是這個測試版本。因此「歷史雜湊不變」不能代表正式狀態不變；修正需要 Owner 核准。

## 9. 限制

- `verification.json` 整體為 **FAIL**，唯一失敗的檢查是上述一處請求間隔（2.934 秒）。其他檢查（模型、封存、程式範圍、家族界線、禁用標籤、MySQL 寫入路徑、Frozen ledger、OD-29 characterization）都通過。

- 成交稽核只有事後旗標（OD-23）。
- MOPS 財報 200 份抽樣驗證**沒有執行**；財報數值維持 DATA_BLOCKED。
- 總報酬：混合權息事件無法拆出現金股利，384 組全部 TOTAL_RETURN_BLOCKED。
- Native7 使用原始價格，Track B 使用還原價格，兩者不能直接比較。
- Native7 的規則參數過去曾調整，但試驗次數不明，所以無法做選擇偏差修正。
- 本輪之前的 Native7 回測沒有設定滑價種子，逐值不可重現；OD-29 之後的重跑固定種子 20261023。
- 疑似暫停交易只依成交型態判斷，沒有官方暫停公告佐證。

## 10. 待 Owner 決定

1. 修正 `test/test_run_daily_persistence.py`，避免測試寫入正式狀態檔，並調整這個動態檔案的雜湊記錄方式。
2. MySQL 匯入端把官方 `--` 存成 NULL（目前是 0），以及外資、投信的對帳錯誤。
3. 月營收公告時間來源、財報全量驗證、現金股利拆分：這三項決定 6 個 DATA_BLOCKED 與全部總報酬的解除。
4. 是否把 Native7 與 Track B 統一成同一種價格基礎，以及重新訓練規格 `retraining-spec-v1` 的簽核。
5. g4 集中度門檻的定義（逐字解讀會讓所有設定失敗）。
