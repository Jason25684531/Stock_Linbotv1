# Native7 估值修復報告（OD-29）

狀態：**修復、驗收測試與五策略重跑均已完成。** 本報告的結果都屬於開發期研究，不是 Fresh OOS。

## 1. 修改內容（`core/backtest/runner.py`）

| 項目 | 內容 |
|---|---|
| 新參數 | `BacktestEngine(..., valuation_policy='legacy')`；允許值 `legacy`、`finite_positive_stale` |
| 預設 | `legacy`；CLI、Web、LINE Bot、scheduler 沒有傳這個參數，行為不變 |
| 限制 | `finite_positive_stale` 只能搭配 `next_session_open_v2` 與 `research_data`；其他值或組合會拋出 `ValueError` |
| 共用估值 | 單策略迴圈與組合迴圈都改呼叫 `_mark_position()`；legacy 分支保留原本三行邏輯 |
| 研究政策 | 只有有限且 > 0 的 `close_price` 會更新估值價；否則沿用最後有效收盤價，並記錄 `STALE_VALUATION` 事件 |
| 事件欄位 | 日期、股票、原因（`NO_BAR_ROW`、`NO_VALID_CLOSE_WITH_VOLUME`、`NO_VALID_CLOSE_NO_VOLUME`、`NON_POSITIVE_CLOSE`）、原始收盤值、沿用價格、價格來源日期、連續沿用天數、持股、市值、占當日權益比例 |
| 沒有有效價格 | 進場以來沒有任何有效收盤價時拋出 `VALUATION_BLOCKED`；不寫 0，也不寫 NaN |
| 期末結算 | 研究政策下，`research_data.last_close` 無效時改用同一個最後有效收盤價；仍無效則照舊拋出 `MISSING_END_MARK` |
| 沒改的部分 | 下單、成交（開盤價檢查）、出場訊號（仍讀原始收盤價）、成本、滑價、部位大小（以現金計） |

沿用價格只用於帳面估值，不會被當成成交價格。測試會檢查沒有任何成交事件使用沿用價格。

## 2. 開發方式與保全

- 修改期間仍有舊程序在執行，因此在獨立 worktree（`Stock_Linbotv1.worktrees/od29`）開發與測試；主工作區的 `core/` 在整個過程中都等於 HEAD。
- 修改前已保存：`od29_valuation/baseline.json`（7 個舊模型雜湊不變且皆為 `MODEL_BLOCKED`；5,463 筆歷史雜湊 0 筆變動；只有既有核准的 `growth_momentum_breakout.py` 例外；Broker `DISABLED`）與 `od29_valuation/preserved_runs.json`（23 個 native7 檔案的 SHA-256、6 個執行中程序與程式版本）。

## 3. Legacy characterization

**合成情境（`test/test_valuation_policy.py`，fixture 由修改前的引擎產生）**：9 個情境：有效、缺列、NaN（有量／無量）、inf、0、負數、連續 3 日無效、進場以來沒有有效價格。修改後的預設與明確指定 `legacy` 都與 fixture 完全相同（每日資產逐值相同、交易與成交事件相同、錯誤相同）。

這份 fixture 也記錄了 legacy 原本的行為：

| 情境 | legacy 當日權益 | 結果 |
|---|---|---|
| NaN 收盤 | NaN | `calculate_metrics` 拋出 `numerator` 例外 |
| inf 收盤 | inf | 同上；且 inf 觸發停利訊號（出場邏輯本來就讀原始收盤價） |
| 0 收盤 | 799,736（前一日 1,002,686） | 無錯誤，單日權益下跌約 20% |
| 負數收盤 | 797,756 | 無錯誤，以負價估值 |
| 缺列 | 沿用最後價格 | 若最後價格是 NaN，NaN 會延續 |

**真實資料重播（2023-04-03～2023-06-30，`od29_valuation/replay_*.json`）**：

- 第一次比較 FAIL（`replay_comparison_unseeded_FAIL.json`）。原因是引擎的滑價取自未設定種子的全域 `random`（`runner.py:653`），所以兩次執行的成交價本來就不同；訊號、日期、開盤價則完全相同。這表示**先前所有 Native7 回測（包括已完成的 defensive）都不是逐值可重現的**。
- 改以固定種子 `SLIPPAGE_SEED = 20261023` 重做後（`replay_comparison.json`）：

| 檢查 | mean_reversion | defensive_low_volatility |
|---|---|---|
| legacy 修改後 = 修改前（每日資產、交易、成交、錯誤） | PASS | PASS |
| 研究政策的交易與成交 = legacy | PASS | PASS |
| 研究政策權益不同的天數 | 1（2023-05-11：NaN → 975,399.96） | 0 |
| STALE_VALUATION 事件 | 1（1225，3,378 股 × 60.70 = 205,044.60，占當日權益 21.0%） | 0 |

結論：`LEGACY_CHARACTERIZATION = PASS`、`ORDERS_UNCHANGED_BY_STALE_POLICY = PASS`。

## 4. 測試

- `test/test_valuation_policy.py`：legacy 對 fixture、明確 legacy = 預設、研究政策權益全為有限值且成交不變、有效價格與 legacy 完全相同、單日無效的事件內容（6 種原因）、連續沿用天數、沒有有效價格時 `VALUATION_BLOCKED`、參數驗證。
- `test/test_native7_rules_only.py`：指標與既有公式一致、邊界情況、增量保存、合併規則、沿用估值敏感度的精確性、除權息與下市旗標。
- 完整回歸：見第 7 節。

## 5. 其他發現

- **MySQL 的 12 列 0 元收盤價**：全部是官方 `--`（當日無整股成交）被 MySQL 寫成 0（2025-12-24、2025-12-26，9 檔股票）。legacy 正式路徑持有這些股票時會以 0 元估值，權益仍是有限值但會錯誤下跌。這不在本次修復範圍（legacy 預設不變），建議另案處理。
- **缺價筆數**：回測期間 1,772 列；先前的 2,041 列包含 2022-08-01～2023-01-02 的回看期（差 269 列）。

## 6. 五策略重跑結果

所有重跑：2023-01-03～2026-05-14（807 個報酬日）、`valuation_policy=finite_positive_stale`、滑價種子 20261023、原始價格（價格報酬），基準為加權股價指數（同期 +193.5%）。程式版本 `native7_rules_only.py` SHA-256 `c2ebad24eb77…`、`runner.py` `82cfbdcaebad…`，與主工作區相同。明細：`od29_valuation/native7_od29_results.csv`、`native7_od29_results.json`；每筆沿用事件：`native7_od29/valuation_events_*.csv`。

### 6.1 績效（研究政策）

| 策略 | 完成 | 成交 | 平倉 | 總報酬 | Sharpe | MDD | CAGR | 勝率 | 超額 Sharpe（對價格指數） | 超額 Sharpe 95% 區間 |
|---|---|---|---|---|---|---|---|---|---|---|
| defensive_low_volatility | 是 | 421 | 209 | −21.09% | −0.541 | −31.73% | −7.13% | 43.1% | −2.005 | −3.118～−0.787 |
| mean_reversion | 是 | 667 | 332 | −33.55% | −0.620 | −42.22% | −11.98% | 40.7% | −2.048 | −3.130～−0.909 |
| hybrid_trend_rank | 是 | 474 | 236 | −69.64% | −1.308 | −69.65% | −31.08% | 30.9% | −2.549 | −3.556～−1.352 |
| institutional_flow_confirmation | 是 | 411 | 204 | −12.14% | −0.337 | −26.51% | −3.96% | 39.2% | −1.944 | −3.085～−0.656 |
| quality_value_low_volatility | 是 | 363 | 180 | −35.47% | −1.081 | −37.33% | −12.78% | 35.0% | −2.368 | −3.611～−0.931 |

CAGR 與勝率取自 `core.backtest.metrics.calculate_metrics`（權益曲線全為有限值，已不再崩潰）。超額 Sharpe 區間為 moving block bootstrap（區塊 20、2,000 次、種子 20261023）。

### 6.2 沿用估值

| 策略 | STALE_VALUATION 次數 | 原因 | 最長連續沿用 | 最高占權益 | 沿用日占交易日 | 旗標 |
|---|---|---|---|---|---|---|
| defensive_low_volatility | 0 | — | — | — | 0% | — |
| mean_reversion | 6 | 無量 5、有零股量 1 | 2 | 32.4% | 0.74% | MATERIAL、PROBABLE_SUSPENSION |
| hybrid_trend_rank | 1 | 無量 1 | 1 | 48.2% | 0.12% | MATERIAL、PROBABLE_SUSPENSION |
| institutional_flow_confirmation | 2 | 有零股量 2 | 2 | 18.3% | 0.25% | MATERIAL |
| quality_value_low_volatility | 5 | 有零股量 4、無量 1 | 2 | 29.8% | 0.62% | MATERIAL、PROBABLE_SUSPENSION |

- 沒有任何沿用期間跨越除權息日，也沒有任何沿用以下市結束（`EX_RIGHTS_DURING_CARRY`、`DELISTING_EXIT_UNVERIFIED` 都沒有出現）。
- **疑似暫停交易**（前 20 個交易日中至少 15 天有成交、當日完全零成交；沒有官方暫停公告佐證）：mean_reversion 的 6955（2025-09-04）與 5906（2025-11-18、11-20、11-24、11-25）；hybrid_trend_rank 的 3036（2025-07-15）；quality_value_low_volatility 的 6955（2026-03-25）。
- 「MATERIAL」的原因是單一持股占權益 18%～48%（策略只持有 3 檔左右），不是沿用時間長：每次最多 2 個交易日，所有沿用日合計不到交易日的 1%。

### 6.3 沿用價格的敏感度

沿用價格只影響當天的帳面估值（下單以現金計、出場讀原始收盤價），所以替代假設下的權益曲線可以逐日精確算出。

| 策略 | 指標 | 沿用前一收盤（採用） | 每個沿用日跌停（−10%／日） | 沿用部位當日視為 0 |
|---|---|---|---|---|
| mean_reversion | Sharpe／MDD | −0.620／−42.22% | −0.578／−44.17% | 0.115／−60.16% |
| hybrid_trend_rank | Sharpe／MDD | −1.308／−69.65% | −1.290／−69.65% | −0.304／−78.00% |
| institutional_flow_confirmation | Sharpe／MDD | −0.337／−26.51% | −0.326／−26.51% | −0.118／−26.51% |
| quality_value_low_volatility | Sharpe／MDD | −1.081／−37.33% | −0.982／−37.33% | −0.023／−53.60% |

- 三種假設的總報酬完全相同：沿用日都不是最後一天，次一交易日恢復正常價格後，權益回到同一條路徑。
- 「視為 0」時 Sharpe 反而上升，這是**算法造成的假象**：單日 −30% 後隔日 +43% 會拉高日報酬的算術平均。這個情境應看 MDD（最多惡化 18 個百分點），不能把它的 Sharpe 當成績效。
- 跌停假設（較合理的不利情境）下，Sharpe 變動不超過 0.10，MDD 最多多 1.95 個百分點；結論（全部為負、全部大幅落後基準）不受影響。

### 6.4 修復前後差異

| 策略 | 修復前（未設種子） | legacy 重跑（同一種子） | 研究政策 | 成交與 legacy 相同 |
|---|---|---|---|---|
| defensive_low_volatility | DIAGNOSTIC，−21.33% | COMPUTED，−21.09% | −21.09%（差 0） | 是 |
| mean_reversion | BLOCKED（6 個 NaN 日） | UNDEFINED（6 個 NaN 日） | −33.55% | 是 |
| hybrid_trend_rank | BLOCKED（1 個 NaN 日） | UNDEFINED（1 個 NaN 日） | −69.64% | 是 |
| institutional_flow_confirmation | BLOCKED（2 個 NaN 日） | UNDEFINED（2 個 NaN 日） | −12.14% | 是 |
| quality_value_low_volatility | BLOCKED（5 個 NaN 日） | UNDEFINED（5 個 NaN 日） | −35.47% | 是 |

- defensive 修復前後差 0.24 個百分點，全部來自未設種子的滑價；同一種子下 legacy 與研究政策完全相同。
- 其他 4 個策略在 legacy 下沒有任何可比較的數字（NaN），所以「差多少」只能說：修復前不可計算，修復後可計算。
- 修復前的成交筆數與研究政策相同或只差 2 筆（hybrid 476 對 474、institutional 413 對 411），差異來自未設種子的滑價使現金略有不同，不是估值政策造成的。

### 6.5 狀態

| 策略 | DATA | BACKTEST | EVIDENCE | 原因 |
|---|---|---|---|---|
| defensive_low_volatility | DATA_PASS | DIAGNOSTIC | INSUFFICIENT | 規則參數曾在早期資料調整（試驗次數不明）、價格報酬基礎、未做成交稽核、沒有模型 |
| mean_reversion | DATA_PASS | DIAGNOSTIC | INSUFFICIENT | 同上＋沿用估值重大、疑似暫停交易 |
| hybrid_trend_rank | DATA_PASS | DIAGNOSTIC | INSUFFICIENT | 同上＋沿用估值重大、疑似暫停交易 |
| institutional_flow_confirmation | DATA_PASS | DIAGNOSTIC | INSUFFICIENT | 同上＋沿用估值重大 |
| quality_value_low_volatility | DATA_BLOCKED（財報數值未驗證） | DIAGNOSTIC | INSUFFICIENT | 同上＋沿用估值重大、疑似暫停交易；財報只到 2026-05-14 |

沒有任何策略升級為 DEVELOPMENT 或 CANDIDATE。權益變成有限值只代表「可以計算」，不代表回測可信。

## 7. 驗收

| 檢查 | 結果 |
|---|---|
| Legacy characterization（合成 9 情境、真實資料重播，同一種子） | PASS |
| 研究政策不改變交易與成交（重播逐筆比較；全期間 5 策略成交、平倉與未成交統計相同） | PASS |
| 新測試 `test_valuation_policy.py`、`test_native7_rules_only.py` | 通過（見 `test_results.json`） |
| 主工作區完整回歸 | 243 passed、3 skipped、1 xfailed、0 failed（`od29_valuation/regression_main.log`） |
| 7 個舊模型雜湊、MODEL_BLOCKED | 不變（`od29_valuation/baseline.json`、`verification.json`） |
| 歷史封存 SHA-256 | 不變；只有既有核准的 `growth_momentum_breakout.py` 例外 |
| 正式狀態檔 | 見 `verification.json` 的 `runtime_status_file`；沒有新增例外 |
| MySQL | 只讀取快照與 SELECT；沒有寫入路徑 |
| Broker | DISABLED；沒有訓練、沒有 recovered 切換、沒有 push |

## 8. 結論

1. **是否消除 NaN 根因**：研究政策下是。5 個策略的權益曲線全為有限值，所有無效收盤價都以有紀錄的沿用估值處理。legacy 預設刻意不變，所以 legacy 路徑仍會產生 NaN（以及 MySQL 0 元估值）。
2. **是否完整執行**：5 個都完整執行。
3. **績效差多少**：defensive 差 0（同一種子）；其他 4 個修復前不可計算。
4. **仍受估值不確定性影響的**：mean_reversion、hybrid_trend_rank、quality_value_low_volatility（疑似暫停交易，且單一部位占 30%～48%）與 institutional_flow_confirmation（18%）。影響集中在 MDD，不影響總報酬與「全部大幅落後基準」的結論。
5. **legacy 的 12 筆 0 元收盤價**：建議另案修復。正式 legacy 路徑持有那些股票時會以 0 元估值，產生無錯誤的假回撤；修法應在 MySQL 匯入端把官方 `--` 存成 NULL（或不寫入），而不是改變 legacy 估值。需要 Owner 另行核准。
