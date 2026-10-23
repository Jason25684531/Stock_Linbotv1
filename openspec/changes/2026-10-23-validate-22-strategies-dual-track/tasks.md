# 2026-10-23 Tasks

OUT=outputs/dual_track_validation/2026-10-23（只新增；已存在的檔案不覆寫）

## Phase 0 — 基準與完整性事件
- [x] 0.1 `python scripts/dual_track_validation.py baseline`：Git HEAD 與工作區、模型雜湊、7 個 MODEL_BLOCKED、5 個封存資料夾 manifest 雜湊、5,463 個歷史雜湊、Frozen 指紋與 cutoff、Fresh OOS ledger 雜湊、legacy、Broker DISABLED。
- [x] 0.2 完整性事件追查：`fundamental_production_status.json` 的前後雜湊與內容差異、修改時間、可能的寫入者（排程、容器、程式碼路徑）；結論寫入 `integrity_event.json`。`verify` 的例外清單只含已核准的 V34 一項。
- [x] 0.3 基準回歸：`python -m pytest test/test_official_data_recovery.py test/test_model_data_validation.py test/test_daily_selection_recovery.py test/test_native_recovery.py test/characterization test/test_run_daily.py test/test_native22_engine_contracts.py -q -p no:cacheprovider`。

## Phase 1 — 共同資料認證層
- [x] 1.1 測試先行：放行時間紀錄、單日最多 3 次、連續 5 次失敗停止批次、預算 1,900 含重試、季批次續傳。
- [x] 1.2 （請求間隔有 1 處記錄為 2.934 秒，`verification.json` 判定 FAIL，見報告第 3 節）`download`：Stage 2 的 T86 914 次與 MI_MARGN 958 次（季批次，由舊到新）、TWT49U 2 個範圍、2026-10-23 重新請求、下市清單 1 次；ledger 每筆有 RELEASED 時間且間隔 ≥ 3.2 秒。
- [ ] 1.3 （部分完成：官方與 MySQL 已分開評等；財報 200 份 MOPS 抽樣未執行，財報數值維持 DATA_BLOCKED）`certify`：官方資料逐資料集逐範圍評等；MySQL 另外評等。價格與交易日、法人、融資、財報（200 份抽樣只標 SAMPLE）、月營收（維持 BLOCKED）、股票池與下市、除權息與現金股利、基準、成本、成交限制。
- [x] 1.4 驗收：沒有補 0、猜測公告日或刪股；涵蓋不全的範圍不得 DATA_PASS。

## Phase 2 — Track A
- [x] 2.1 測試先行：`dealer_consec_days` 歷史連買；17 個特徵完整案例的 z-score 母體；只含上市代號；先算特徵再套規則；規則版不因缺 AI 特徵排除；契約讀取 `training_data_end` 與規格雜湊；`train` 無簽核檔拒絕；訓練資料與 recovered 逐值相等。
- [x] 2.2 實作 6 項修改；新行為放在新版本欄位或新函式，舊封存結果與 legacy 路徑不變。
- [x] 2.3 回歸：legacy characterization 全數通過；7 個舊模型雜湊不變；研究端結果有變時以新版本與新 manifest 保存。
- [x] 2.4 `track_a_readiness.json`。

## Phase 3 — Track B 公式與資料資格
- [x] 3.1 `track_b_formula_checks.json`：Day3 四因子、A191_132 近似與標準的差異、D5 五因子視窗、G2/G3 公告時間與修訂。
- [x] 3.2 計時探測（各家族依識別碼排序的第一組固定設定，不看績效）→ `timing_probe.json`，依 OD-25 決定重跑範圍。
- [x] 3.3 重跑並截斷到各家族上界；384 組執行清單 `run_list_384.csv`（DONE／PENDING／BLOCKED 與原因）；Day3 嚴格 OOS 視窗沒有任何讀取。

## Phase 4 — 回測與統計
- [x] 4.1 成交稽核：1%／2%／5% 三情境、漲跌停鎖死、停牌、下市（DELISTING_EXIT_UNVERIFIED）；只標記，不修改引擎。
- [x] 4.2 指標、2 倍成本、價格報酬與（可驗證時的）總報酬、兩種 TAIEX 基準、多空分段、集中度、bootstrap、DSR（保守試驗次數）→ `backtest_results.csv`、`statistical_evidence.json`。
- [x] 4.3 多因子與單因子差異的信賴區間 → `factor_combination_comparison.csv`。
- [x] 4.4 測試：DSR、bootstrap、集中度、門檻判定與等級（DEVELOPMENT／DIAGNOSTIC／BLOCKED）、禁止出現 OOS_PASS／PROFITABLE_PROVEN／PRODUCTION_READY。

## Phase 5 — AI 與非 AI 比較
- [x] 5.1 `rules_only` 與測試；Native7 規則版診斷回測（只在資料合格範圍）。
- [x] 5.2 `comparison_plan.md`。

## Phase 6 — 交付
- [x] 6.1 `strategy_status_matrix.csv`、`docs/dual_track_validation_report.md`、`integrity_report.md`。
- [x] 6.2 `test_results.json`、`reproduction_manifest.json`、`verification.json`。
- [x] 6.3 `git diff --check`、`openspec validate 2026-10-23-validate-22-strategies-dual-track --strict`；程式與小型證據分開提交，不 push。

## Phase 7 — OD-29 估值修復（2026-10-24）
- [x] 7.1 保全：`od29_valuation/baseline.json`、`preserved_runs.json`、`preserved_runs_final.json`；舊程序執行期間在獨立 worktree 修改，主工作區 core 不動。
- [x] 7.2 Legacy characterization：修改前的引擎產生 `test/fixtures/baseline/valuation_legacy_characterization.json`；真實資料重播（固定滑價種子）`od29_valuation/replay_comparison.json` 為 PASS／PASS。
- [x] 7.3 `core/backtest/runner.py` `valuation_policy`（預設 legacy）與 `test/test_valuation_policy.py`。
- [x] 7.4 Native7 五策略重跑（研究政策與 legacy 各一組，種子 20261023）→ `native7_od29/`、`native7_od29_legacy/`、`od29_valuation/native7_od29_results.*`。
- [x] 7.5 `native7_nan_root_cause_report.md`、`native7_valuation_fix_report.md`；Matrix 只採用 `native7_od29/`。
