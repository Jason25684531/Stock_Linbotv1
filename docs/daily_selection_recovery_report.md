# 每日選股修復報告（Daily Selection Recovery）

- 日期：2026-10-22
- OpenSpec：`openspec/changes/2026-10-22-recover-daily-selection-pipeline/`
- 輸出：`outputs/daily_selection_recovery/2026-10-22/`（只新增，不覆寫。`_aborted/` 存放兩類檔案：一次因改碼而中止的 shadow 部分輸出，以及三版在腳本定版前產生、之後被取代的報告）
- 基準：HEAD `1e43f1d`；程式提交：`4bb3911`、`b2f62ac`、`62091f3`、`5571f5e`、`9b073dd`（未 push）
- 相關文件：`docs/daily_selection_before_after.md`、`docs/daily_selection_data_gaps.md`

**結論**：四項每日選股缺陷都已在程式中修復，並有獨立測試與修復前後的實際數據。研究回測與 recovered 每日選股現在呼叫同一個特徵核心，在相同的官方輸入下 7 個策略都是 PARITY_PASS，而且每個策略都有非空候選。正式環境維持 legacy：recovered 只能 dry-run，Broker 下單仍是 DISABLED。7 個模型因為沒有 `training_data_end`，而且訓練時 `chip_score` 的口徑無法證實，所以都是 MODEL_BLOCKED；財報、月營收、上櫃也還是 DATA_BLOCKED。目前仍不能產生有效的投資回測。

## 最終摘要

### 1. 四個主要缺陷是否真的修復？

是，且每項都有獨立測試與實際 Before／After 數據（`test/test_daily_selection_recovery.py`；數字來自 `feature_comparison_summary.csv`）。

| 缺陷 | 根因 | 修復（程式位置） | 測試 | 實際 Before → After |
|---|---|---|---|---|
| P0-1 `volume_ratio` 恆為 1.0 | `run_daily` 先取當日，再對單日算 20 日均量 | `core/calc_indicators.py:613` `build_feature_history`：在逐股歷史上呼叫訓練用的 `calculate_ratio_features`（:651），有效觀測不足 20 筆則為 NULL（:609，OD-1） | `test_volume_ratio_follows_twenty_session_history_and_differs_by_stock`、`test_volume_ratio_needs_twenty_observations`、`test_future_sessions_never_change_past_features` | 22,995 筆全為 1.0 → 22,632 個不同值，101 筆 NULL |
| P0-2 `chip_score` 恆為 0 | 來源 NULL → NaN → `fillna(0)`；另外 `calculate_margin_change_pct` 與 `calculate_consec_days` 內部也會把缺值變成 0 | 共用核心不補值，並把這兩個函式的隱性 0 遮回 NULL（`calc_indicators.py:642-649`；函式本身不改，因為 legacy 與訓練程式也在用）；公式與權重不變 | `test_chip_score_is_nan_without_dealer_or_margin_and_varies_with_them`、`test_institutional_strategy_sees_no_row_with_missing_chip_score`、`test_institutional_stage_two_really_filters` | 11,985 筆 0 → 0 筆 0、15,603 筆 NULL（來源缺漏，並排除） |
| P0-3 V34 60 日高點退化 | 在單日橫截面上做 `rolling(60)` | `calc_indicators.py:637`：逐股 60 個交易日、含當日、原始價，不足 60 日為 NULL（OD-2）；V34 輸入已有 `high_60` 時直接使用（`growth_momentum_breakout.py:170`），0.93 門檻不變 | `test_high_60_is_the_sixty_session_high_not_todays_high`、`test_v34_uses_history_high_and_keeps_its_threshold` | 20,983 筆改變，1,718 筆 NULL；V34 候選 4,430 → 1,786 |
| P0-4 NULL 補 0 產生假訊號 | `run_daily` 的指標、財報、營收補 0；策略內 `fillna(0)` | 策略執行之前，依規則與模型的必要輸入排除缺值股票，並記錄股票、日期、欄位與原因（`core/research/native_frame.py:78`、`:141`）。策略程式內的 `fillna(0)` 因此碰不到缺值；財報與營收不補值 | `test_missing_indicators_are_recorded_with_reason`、`test_zero_filling_would_create_a_false_signal_but_exclusion_prevents_it`（既有） | `ma60` 156 筆 0 → NULL 並排除；`kd_k` 44 筆 → NULL，3 筆合法的 0 保留 |

### 2. 7 個策略是否都能用正確資料與條件進行每日選股？

**計算與條件：可以。資料與模型：還不行。**

- 條件完整性：7 個策略都只用齊全的輸入執行全部條件，缺值在策略之前排除。V36 第二階段確實有執行：受控測試 `test_institutional_stage_two_really_filters` 預期只有 1101 通過，實際也只有 1101 通過；官方輸入 2026-05-21 的紀錄是 Stage 1 335 → Stage 2 72 → Stage 3 51 → Stage 4 48。MySQL 2026-10-13～16 每日也有 65～71 檔真實候選。
- 資料：用 MySQL 跑的每日選股是 DATA_FAIL。MySQL 外資／投信在研究期有 19 個整日存成 0，10 月投信有 53 筆和官方不符；上櫃未認證；財報與月營收是 DATA_BLOCKED。研究端已改用官方 T86／MI_MARGN，這兩個閘門為 DATA_PASS。
- 模型：7 個模型都是 MODEL_BLOCKED（`model_contract_report.json`）。依 OD-5，沒有有效 AI 分數時不推送；recovered 依 OD-7 只能 dry-run。

### 3. 修復後每日選股與研究回測是否一致？

一致。輸入相同（`research_snapshot_official_chips.parquet` 加 PIT 財報）、日期也相同（5 個）時，`run_daily` 的 recovered 程式路徑和研究引擎的 `find_candidates` 在 7/7 策略上前 3 名與必要輸入清單完全相同，而且全部有非空候選（6 個策略 5/5 天，hybrid 4/5 天）。證據在 `parity_results.json`。

兩邊不再各自維護公式：`NativeResearchData.cross_section`（`native_frame.py:114`）與 `run_daily.load_recovered_data`（`jobs/run_daily.py:224`）都呼叫 `build_feature_history`。PIT 財報（`prepare_fundamentals`，`native_frame.py:52`）與缺值排除（`frame`）也是同一份程式。測試 `test_recovered_daily_frame_equals_research_frame` 驗證兩邊的 frame 逐值相等。

### 4. 有哪些問題因缺少資料或 Owner 決策而尚未解除？

詳見 `outputs/daily_selection_recovery/2026-10-22/owner_decisions_required.md`。

| ID | 問題 | 狀態 |
|---|---|---|
| OD-3a／3b | 模型沒有 `training_data_end`；訓練時自營商／融資全為 NULL，`chip_score` 實際上只隨外資與投信變動（`dealer_ratio_z` 重要性 0，`chip_score_z` > 0） | MODEL_BLOCKED，不重新訓練 |
| OD-5a | 模型 BLOCKED 時，recovered 沒有可推送的排名 | 只在 shadow 執行 |
| OD-7a | 是否把正式模式切換到 recovered | 維持 legacy |
| OD-8 | live recovered 仍會讀到 MySQL 的上櫃列 | 未認證，已在報告列出 |
| OD-9 | MySQL 法人資料整日存成 0、自營商／融資歷史為 NULL | 研究改用官方資料；正式 DB 未修改 |
| OD-10 | V36 放寬模式會略過連買條件；資料正確後，中性分數約 50，高於放寬門檻 27.5 | 規則未改，已有測試記錄這個行為 |
| OD-11 | 原始價的 `high_60` 在除權息後會偏高 | 依 OD-2 維持原始價 |
| OD-12 | 月營收、FY2026 以後的財報沒有公告時間 | DATA_BLOCKED |
| OD-13 | 模型才需要的輸入缺值也會排除規則候選，候選數因此受模型可得性影響 | 保持與研究一致 |

### 5. 距離 7 個原生策略的有效投資回測，還缺什麼？

1. **模型**：記錄 `training_data_end`；釐清訓練時的 `chip_score` 口徑，或經 Owner 核准後，用認證過的籌碼資料重新訓練（OD-3）。
2. **樣本**：模型訓練後的有效樣本仍少於 252 個交易日（native7：47 個），OOS_BLOCKED 不變。
3. **財報與月營收**：需要 FY2026 以後的實際公告時間，月營收需要官方來源（OD-12）。
4. **日曆與行情**：官方 MI_INDEX 快取要延伸到 2026-07-28 之後；上櫃要認證（OD-8）。
5. **正式資料庫**：以官方 T86／MI_MARGN 修復 MySQL 的整日 0 與 NULL（OD-9），每日選股才能用 DATA_PASS 的資料運行。
6. **總報酬**：現金股利尚未入帳（native7 既有限制）。

## 1. 安全切換（OD-7）

- `config/settings.py:99` 新增 `DAILY_FEATURE_MODE`，預設為 `legacy`。`run_daily --feature-mode {legacy,recovered}` 未指定時讀取這個設定。
- `jobs/run_daily.py:239` `resolve_feature_mode`：只要是 recovered 且沒有 `--dry-run`，就直接拒絕執行，即使環境變數已設為 recovered；而且在寫入任何 pipeline 狀態之前就拒絕（`:1131`）。測試：`test_recovered_mode_is_dry_run_only`。
- legacy 路徑：`_load_market_history` 是從原函式原樣搬出，指標計算、補值與回寫都沒有改；Scheduler、LINE、Web、CLI 的預設行為不變。characterization 測試全部通過。
- recovered 只讀取原始欄位（`run_daily.py:49`），legacy 回寫到 `daily_market_data` 的指標與 `revenue_yoy` 一律忽略（`test_recovered_daily_frame_equals_research_frame` 會注入這些欄位來驗證）。
- recovered 評分（`run_daily.py:735` 起）：只使用嚴格模型契約，不 fallback、不補特徵；特徵有缺值時直接 raise（`MODEL_FEATURE_MISSING_VALUE`），契約被拒絕時不評分並記錄 `ai_status`。測試：`test_recovered_scoring_never_fills_features`。

## 2. 實際測試與驗證

| 指令 | 結果 |
|---|---|
| 修改前回歸（`baseline.log`）：characterization、run_daily、persistence、daily parity、pipeline contract、native recovery、native22、next session、strategy center | 117 passed、3 skipped、1 xfailed |
| Phase 3 後的同一組加上新測試 | 130 passed、3 skipped、1 xfailed |
| `python scripts/daily_selection_recovery.py tests`（12 組，含 research execution price、strategy search；`test_results.json`） | 147 passed、3 skipped、1 xfailed、0 failed |
| `python scripts/daily_selection_recovery.py parity` | 7/7 PARITY_PASS，全部有非空候選 |
| `python scripts/daily_selection_recovery.py verify`（`verification.json`） | VERIFY PASS |
| `git diff --check`、`openspec validate 2026-10-22-recover-daily-selection-pipeline --strict` | 通過（見第 5 節） |

保全驗證的內容：5,463 個歷史雜湊未變；22 個家族、384 組設定、453 個身分、461 筆來源；Frozen Candidate 指紋未變，cutoff 為 2026-07-28；native7（29 個）與 native22（17 個）已封存的輸出都和各自的 manifest 雜湊一致；`execution/`、`core/runtime/`、`core/backtest/`、`jobs/scheduler.py`、`app/`、`services/`、`strategy_settings.json`、`4_run_backtest.py`、`5_push_to_line.py` 自 `1e43f1d` 起沒有改動；策略參數沒有改（`config/settings.py` 只新增了 `DAILY_FEATURE_MODE`）；`BROKER_ORDER_SUBMISSION=DISABLED`。

74 個受保護來源中，唯一有變動的是 V34 策略檔（P0-3 的修正）。`verify` 會逐行檢查這個 diff：只新增了一行說明註解與 `if 'high_60' not in df_sorted.columns:`，原本的程式碼完整保留在條件內。同時確認 DB 沒有 `high_60` 欄位。因此 legacy 輸入（單日 Daily frame 與 DB 回測列）的行為完全一樣，歷史結果可以原樣重現。

## 3. 程式修改位置

| 檔案 | 內容 |
|---|---|
| `core/calc_indicators.py` | 新增 `build_feature_history`（共用核心）；既有函式未改 |
| `core/research/native_frame.py` | 改為呼叫共用核心；新增 `prepare_fundamentals`（實際公告日優先）、`load_publication_dates`、逐筆排除紀錄；`FRAME_INPUTS` 加入 `high_60` |
| `core/research/native_models.py` | identity 記錄 `training_data_end`（沒有時為 null，不推定） |
| `core/strategies/growth_momentum_breakout.py` | 輸入已有 `high_60` 時直接使用 |
| `jobs/run_daily.py` | `--feature-mode`、`resolve_feature_mode`、`load_recovered_data`、`recovered_strategy_frame`、recovered 評分；legacy 不變 |
| `config/settings.py` | `DAILY_FEATURE_MODE`（預設 legacy） |
| `scripts/daily_selection_recovery.py`（新） | baseline、chips、reconcile、shadow、contracts、parity、tests、report、verify |
| `test/test_daily_selection_recovery.py`（新，13 項）、`test/test_native_recovery.py` | 新測試；後者改用正值融資餘額，比較完整輸入下的指標（餘額為 0 時變動率無定義，legacy 補 0，新核心保留 NULL） |

## 4. 對已封存研究的影響

native7 的研究端（`NativeResearchData`）現在也使用共用核心，所以重跑時 V34 的 `high_60`、零成交日的處理、財報可得日（實際公告日優先）都會和 2026-10-22 native7 的封存結果不同。封存檔案沒有重算，也沒有覆寫（verify 已檢查）；要重現封存結果，請使用提交 `1e43f1d`。

## 5. 重現方式

```bash
export PYTHONIOENCODING=utf-8
# 修改前的 legacy 基準：在 1e43f1d 的程式上執行 b2f62ac 版本腳本的 baseline 階段（腳本當時尚未提交）
python scripts/daily_selection_recovery.py baseline
python scripts/daily_selection_recovery.py chips      # 官方 T86/MI_MARGN（已快取者不重抓）
python scripts/daily_selection_recovery.py reconcile  # 對帳、官方籌碼研究快照、資料閘門
python scripts/daily_selection_recovery.py shadow     # recovered dry-run
python scripts/daily_selection_recovery.py contracts  # 模型契約與 chip_score 訓練追溯
python scripts/daily_selection_recovery.py parity     # 每日選股 vs 研究回測
python scripts/daily_selection_recovery.py tests
python scripts/daily_selection_recovery.py report
python scripts/daily_selection_recovery.py verify
```

輸出目錄已存在時，腳本會拒絕覆寫。MySQL 的 dry-run 是在執行當下讀取資料庫，資料庫之後若有變動，結果不會逐位元相同；官方與研究快照的輸入雜湊記錄在 `reproduction_manifest.json`。

## 6. 證據保存（沿用上一輪的方案，等你核准）

`docs/`、`openspec/`、`outputs/`、`artifacts/` 都被 `.gitignore` 忽略，我沒有改 `.gitignore`。程式與測試已提交。建議只對小型報告與 manifest 使用 `git add -f`：三份 docs、`reproduction_manifest.json`、`data_quality_report.json`、`model_contract_report.json`、`parity_results.json`、`owner_decisions_required.md`、`verification.json`、`official_chip_manifest.json`。官方原始快取（51 MB）、兩個 parquet 與逐日 log 不進 Git，由 manifest 的雜湊驗證。
