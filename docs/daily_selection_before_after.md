# 每日選股修復前後對照

- 日期：2026-10-22
- 修復前：`before_legacy/`，在 HEAD `1e43f1d` 改程式之前執行現行 `run_daily --dry-run`
- 修復後：`after_recovered/`，執行 `run_daily --dry-run --feature-mode recovered`
- 兩者讀同一個 MySQL，日期也相同：2026-05-21、06-04、06-18、07-03、07-20（研究期），以及 2026-10-13～10-16、10-20（MySQL 已有自營商／融資的日期）
- 逐筆資料：`feature_comparison.csv`（22,995 個股票日）、`feature_comparison_summary.csv`、`strategy_selection_comparison.csv`
- 這些都是 dry-run 的工程證據，不是投資績效。

## 1. 特徵修復前後（10 個日期合計，共同 22,995 個股票日）

| 特徵 | 修復前 | 修復後 | 說明 |
|---|---|---|---|
| `volume_ratio`（P0-1） | 22,995 筆全部等於 1.0；每日只有 1 個值 | 22,632 個不同值；101 筆為 NULL（有效成交量不足 20 筆） | 20 個交易日的訓練公式，在逐股歷史上計算 |
| `high_60`（P0-3） | 等於當日最高價 | 20,983 筆改變；1,718 筆為 NULL（不足 60 個交易日） | 逐股 60 個交易日、含當日、原始價 |
| `chip_score`（P0-2） | 11,985 筆為 0（5～7 月的 11,454 筆全部是 0） | 15,603 筆為 NULL（來源缺漏），沒有任何 0 | 5～7 月與 10-20 全部為 NULL；10-13～16 每日約 1,400 個不同值 |
| `margin_change_pct` | 16,684 筆為 0 | 15,452 筆為 NULL | 函式內建的 `fillna(0)` 在共用核心中被遮回 NULL |
| `ma60`（P0-4） | 156 筆補成 0 | 156 筆為 NULL 並被排除 | — |
| `kd_k`（P0-4） | 47 筆為 0（其中 3 筆是合法的 0） | 44 筆為 NULL 並被排除，3 筆合法的 0 保留 | 合法的 0 與缺值分開處理 |

## 2. 七個策略的選股差異

候選數與缺值排除數是 10 個日期的合計。前 3 名取 2026-10-15，這天四項籌碼來源都有資料。

| 策略 | 項目 | 修復前 | 修復後 |
|---|---|---|---|
| defensive_low_volatility | 選股候選數 | 0 | 123 |
| | 缺值排除數 | 0（缺值被補 0，不排除） | 2,956 |
| | 前 3 名（10-15） | 無候選 | 6191 2603 3036（診斷用 AI 分數） |
| growth_momentum_breakout | 選股候選數 | 4,430 | 1,786 |
| | 缺值排除數 | 0 | 7,664 |
| | 前 3 名（10-15） | 2466 6278 3597 | 3287 2355 6278（診斷） |
| hybrid_trend_rank | 選股候選數 | 54 | 51 |
| | 缺值排除數 | 0 | 3,069 |
| | 前 3 名（10-15） | 2409 3576 | 2409（診斷） |
| institutional_flow_confirmation | 選股候選數 | 747 | 273 |
| | 缺值排除數 | 0 | 15,614 |
| | 前 3 名（10-15） | 3605 6526 8213 | 6526 8213 6743（診斷） |
| mean_reversion | 選股候選數 | 0 | 876 |
| | 缺值排除數 | 0 | 2,956 |
| | 前 3 名（10-15） | 無候選 | 6526 1560 2030（診斷） |
| quality_growth | 選股候選數 | 4,523 | 2,582 |
| | 缺值排除數 | 0 | 7,070 |
| | 前 3 名（10-15） | 3443 6957 1560 | 3443 3287 3597（診斷） |
| quality_value_low_volatility | 選股候選數 | 800 | 779 |
| | 缺值排除數 | 0 | 5,899 |
| | 前 3 名（10-15） | 2020 1808 3025 | 2020 2436 4527（診斷） |

以下各項 7 個策略狀態相同：

| 項目 | 修復前 | 修復後 |
|---|---|---|
| 模型特徵有效性 | INVALID：特徵補 0、`volume_ratio` 恆為 1.0、`chip_score` 由補 0 的輸入算出 | MODEL_BLOCKED：缺 `training_data_end`，訓練時 `chip_score` 口徑未經證實；5～7 月與 10-20 另有 FEATURE_UNAVAILABLE_AT_SOURCE |
| 策略條件完整性 | DEGRADED：量能條件失效、V34 突破條件退化、缺值補 0 可讓條件成立 | COMPLETE：規則輸入齊全才進入策略，缺值股票在策略之前排除並記錄原因 |
| 工程驗證 | ENGINE_FAIL（P0-1～P0-4） | ENGINE_PASS（13 項新測試，官方輸入 parity 7/7 PASS） |
| 資料驗證（MySQL dry-run） | 未驗證 | DATA_FAIL：MySQL 法人有整日存成 0、10 月投信有 53 筆不符，上櫃未認證，財報與月營收 BLOCKED |
| 可否推送 | 是（正式 legacy） | 否：recovered 只能 dry-run（OD-7），模型 BLOCKED 時不推送（OD-5） |

修復後的前 3 名都是「診斷」排名：模型雖然可以載入，但處於 MODEL_BLOCKED，分數只能用來驗證計算一致，不能當作有效排序。5～7 月與 10-20 這些 MySQL 日期因為自營商／融資來源全為 NULL，嚴格契約拒絕評分，前 3 名標為 NOT_VERIFIABLE（unscored）。逐日數字請看 `strategy_selection_comparison.csv`。

## 3. 差異原因

| 策略 | 主要原因 |
|---|---|
| defensive_low_volatility、mean_reversion | 修復前 `volume_ratio` 恆為 1.0。V37 的「量縮」條件要求 `volume_ratio < 1.0`，永遠不成立；V33 的量比條件也一樣退化。修復後有候選 |
| growth_momentum_breakout | 突破條件改用真正的 60 日高點，比當日高點嚴格（約 −60%）。另外營收缺值或尚未公告的股票被排除 |
| institutional_flow_confirmation | 修復前，`dealer_buy`／`margin_balance` 為 NULL 時被補成 0：5～7 月 `chip_score` 全為 0，所以沒有候選；10-20 融資缺漏，補 0 後放寬模式選出 369 檔。修復後，來源缺漏的日期全部排除（DATA_BLOCKED），10-13～16 則實際執行第二階段 |
| quality_growth、quality_value_low_volatility | 修復前讀「最新一期」財報（前視），且缺值補 0。修復後依可得日取用，缺值排除 |
| hybrid_trend_rank | 規則幾乎不變；差異來自缺值排除 |

## 4. 相同官方輸入下的研究回測對照（`parity_results.json`）

輸入是 `research_snapshot_official_chips.parquet` 加上 PIT 財報。一邊是 `run_daily` 的 recovered 程式路徑（`recovered_strategy_frame` 與 `run_strategy`），另一邊是研究引擎的 `find_candidates`。比較 5 個日期的前 3 名、必要輸入清單，以及是否有非空候選。

| 策略 | Parity | 有候選的日期 | 2026-05-21 前 3 名 | 2026-07-20 前 3 名 |
|---|---|---:|---|---|
| defensive_low_volatility | PARITY_PASS | 5/5 | 2603 2882 2412 | 2884 2812 2845 |
| growth_momentum_breakout | PARITY_PASS | 5/5 | 4916 6449 2472 | 6505 1467 2527 |
| hybrid_trend_rank | PARITY_PASS | 4/5 | 2342 2354 3044 | 2601 |
| institutional_flow_confirmation | PARITY_PASS | 5/5 | 2383 3037 6412 | 4763 1414 4904 |
| mean_reversion | PARITY_PASS | 5/5 | 2368 7750 2383 | 1718 6226 6409 |
| quality_growth | PARITY_PASS | 5/5 | 6449 3443 3090 | 6446 6505 2493 |
| quality_value_low_volatility | PARITY_PASS | 5/5 | 6757 1609 2497 | 2201 2520 2364 |

V36 第二階段確實有執行，`parity_stdout.log` 有逐階段紀錄：2026-05-21 Stage 1 335 檔 → Stage 2 72 檔 → Stage 3 51 檔 → Stage 4 48 檔；2026-06-04 為 430 → 68 → 37 → 30。沒有任何一天進入放寬模式。
