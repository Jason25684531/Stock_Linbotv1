# 22 策略雙軌驗證：封存摘要與剩餘問題交接表

變更：`2026-10-23-validate-22-strategies-dual-track`。依 OD-30～OD-33 處置。證據：`outputs/dual_track_validation/2026-10-23/final_closure/`（簡稱 `FC/`）與 `closure/`（簡稱 `CL/`）。

本摘要**不使用總 PASS**。四種性質的結果分開記錄；`FC/verification_final.json` 是單項檢查的集合。

## 1. 四種狀態

| 狀態 | 內容 |
|---|---|
| **ENGINEERING_WORK_COMPLETED** | 22 個策略 ENGINE_PASS；384 組研究設定全部 DONE；Track A 六項修復；OD-29 研究估值政策與重跑；測試隔離保護；跨程序 `RequestGate`；MOPS 200 份抽樣；MOPS YoY 修正資料集 v3.1；完整測試 **1,273 passed、0 failed**，正式資料夾前後完全不變 |
| **HISTORICAL_COMPLIANCE_EXCEPTION** | `HISTORICAL_RATE_GATE_EVIDENCE_FAIL`：未解決、已接受留存（OD-30，方案 C）。原始 `verification.json` 維持 FAIL；不宣稱 `REQUEST_COMPLIANCE_PASS`；不宣稱已證明是記錄誤差 |
| **DATA_REMEDIATION_PENDING** | G2/G3 維持 `DATA_BLOCKED`；v3.1 資料集已建但沒有任何消費端改用；沒有全量 PIT 認證；Native7 `quality_*` 的財報（MySQL 財報表）、growth_* 的月營收公告時間都未認證 |
| **INVESTMENT_VALIDATION_NOT_PROVEN** | 22 個策略中 0 個有資格申請獨立驗證；384 組全部 `EVIDENCE = INSUFFICIENT`；Native7 規則版回測全部是 DIAGNOSTIC，且都是虧損並大幅落後基準 |
| （營運待辦）**PRODUCTION_STATUS_UNTRUSTED** | 正式狀態檔是測試資料，見第 3 節；沒有宣稱正式環境健康 |

## 2. 22 策略（與原矩陣完全一致，沒有任何評等被改）

| 組 | 策略數 | ENGINE | DATA | BACKTEST | EVIDENCE |
|---|---|---|---|---|---|
| Track A（Native7） | 7 | 7 PASS | 4 PASS、3 BLOCKED（growth_momentum_breakout、quality_growth、quality_value_low_volatility） | 5 DIAGNOSTIC、2 BLOCKED | 7 INSUFFICIENT |
| Track B1（G2/G3） | 3 | 3 PASS | **3 BLOCKED** | 3 DIAGNOSTIC | 3 INSUFFICIENT |
| Track B2（D5） | 1 | 1 PASS | 1 PASS | 1 DEVELOPMENT（44/48 組） | 1 INSUFFICIENT |
| Track B3（Day3） | 11 | 11 PASS | 11 PASS | 6 DEVELOPMENT、5 DIAGNOSTIC | 11 INSUFFICIENT |

來源：`strategy_status_matrix.csv` 與 `CL/strategy_status_matrix_closure.csv`（只有 G2/G3 的說明文字不同）。

## 3. 正式狀態檔（OD-32）

`FC/production_status_check_20261024T163324.json`（只讀檢查，腳本 `scripts/production_status_check.py`）：

- 路徑 `outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/fundamental_production_status.json`；SHA-256 `bb561a27c5559ea2…`；修改時間 2026-10-24 15:13:09（+08:00）。
- 判定 **`PRODUCTION_STATUS_UNTRUSTED`**：內容與 `test_run_daily_persistence` 的輸出逐位元相同；asof 2026-04-28 早於最新官方交易日 2026-10-22。
- 誰在讀它：只有 `core/diagnostics/service.py`（診斷顯示，經 `jobs/strategy_ops.py`、`scripts/revalidate_strategy_history.py`）。正式的選股閘門（`run_daily.run_fundamental_production`）每次依證據檔重新評估，**不讀這個檔案**。
- fail-closed 評估：檔案內容本身是 `production_eligibility = BLOCKED`、`production_enable_flag = false`、`production_ready = NO`、`broker = DISABLED`、`recommendation_count = 0`，診斷服務缺欄位時也預設 `BLOCKED`，所以顯示端不會因為這個檔案而顯示可用。這是依內容與程式碼的判斷，我沒有在執行中的服務上實測。
- 是否已被排程重新產生：檢查時（2026-10-24 16:31）下一次 `evening` 排程（18:30 +08）還沒有執行，所以還沒有。
- 安全措施：沒有覆寫、還原或修改；測試寫入隔離保留。四個證據檔的雜湊與現有證據檔一致。
- **需要人工處理**：排程執行後，不能只看修改時間。請依序確認：`pipeline_runs` 的 `run_daily` 當晚為 success；檔案 `asof_date` 等於最新官方交易日；`python scripts/production_status_check.py record` 的分類不再是 UNTRUSTED。在此之前，不要宣稱正式環境健康。

## 4. 兩個既有測試失敗（OD-33）

| 問題 | 根因 | 處置 |
|---|---|---|
| A：`BacktestEngine` 以 `__new__` 建立時 `find_candidates` 出錯 | **測試前提過時，不是程式回歸。** 手工建引擎的測試沒有設 `research_data`（Native7 那一輪 `f35b7e1` 加進 `__init__`）與 `rules_only`（Track A 那一輪 `95627ca` 加進 `__init__`）；其他用 `__new__` 的測試都有設。正式程式碼一律經 `__init__`，沒有任何執行路徑缺這兩個屬性 | 測試補 `research_data = None`、`rules_only = False`；`runner.py` 沒有修改（OD-29 的雜湊綁定不變） |
| B：`native_frame.py` 匯入 `core.calc_indicators` | **設計衝突，不是程式回歸。** 這個匯入從 `native_frame.py` 建立的第一天（`f35b7e1`）就存在，目的是研究快照與每日選股共用同一份特徵實作；邊界測試是在這個橋接模組出現前寫的，從沒更新。整個 `core/research` 只有這一個檔案違反 | 邊界測試改成釘住這個例外：只允許 `native_frame.py` 匯入 `build_feature_history`、`build_multi_factor_matrix`；任何其他檔案、模組或名稱仍會失敗（已做反向檢查）。程式沒改 |

此項是把一個邊界規則加上明確例外，不是程式修復；是否要把共用特徵核心搬到中性模組，屬於之後的架構決定。完整測試結果：**1,273 passed、4 skipped、4 xfailed、0 failed**。這兩個測試先前都不在回歸清單裡，所以 Native7 與 Track A 兩輪加屬性（`research_data`、`rules_only`）時沒有被發現；其中 `rules_only` 是我自己加的。

## 5. MOPS YoY（OD-31，獨立變更 `2026-10-24-remediate-mops-yoy-basis`）

- 新版資料集 `data/processed/fundamental_pit_v3_yoy_same_basis/`（v3 保持唯讀，雜湊前後不變）：記錄檔、矩陣、`correction_detail.csv`、`manifest.json`、`matrix_manifest.json`。
- 規則：YoY 只有在當期與前一年同季的口徑相同時才存在；其餘移除並列出原因，不補值。
- 移除 **944 筆**：934 筆「前一年季別鏈不完整」（當期單季 ÷ 前一年累計）、10 筆「當年季別鏈不完整」（反向）；其中 `revenue_yoy` 466、`operating_income_yoy` 468、累計版各 5。保留的 YoY 列 0 筆與同口徑公式不符。
- 原始值核對：115,842 筆基礎值全部與快取一致；18,784 筆 Q4 單季（全年減前三季）全部相符。
- 矩陣：用同一方法重建，並先確認「舊記錄重建＝舊矩陣」逐位元相同。`operating_income_yoy` 8,068 格改變（63 檔股票）：7,629 格變空值、439 格退回前一季的同口徑值；`eps_yoy`、`eps`、營收、營業利益、營業利益率沒有任何改變，所以 G3 不受影響。
- **G2/G3 維持 DATA_BLOCKED**：這個資料集仍只有 2026-09-15 的單次抓取、沒有首次公告值、沒有全量 PIT 認證。沒有任何消費端（G2/G3 shadow、因子驗證、Frozen 策略）被切換；Frozen 指紋沒動。

## 6. 剩餘問題交接表

| # | 項目 | 狀態 | 下一步（需要誰決定） |
|---|---|---|---|
| 1 | `HISTORICAL_RATE_GATE_EVIDENCE_FAIL` | 未解決、已接受留存 | 之後任何下載變更：用 `gate_gap_seconds ≥ 3.2` 逐對驗證，不得引用本 ledger 作為合規證據；新閘門尚未在真實下載中使用過 |
| 2 | `PRODUCTION_STATUS_UNTRUSTED` | 營運待辦 | 運維在今晚排程後依第 3 節三項確認；未確認前不宣稱正式環境健康 |
| 3 | G2/G3 `DATA_BLOCKED` | 資料修復待處理 | Owner 決定是否讓 G2/G3 shadow／因子驗證／Frozen 策略改用 v3.1；取得首次公告值或第二來源後做全量 PIT 認證 |
| 4 | Native7 `quality_*`、growth_* 資料 | BLOCKED | MySQL 財報表的全量驗證；月營收實際公告時間 |
| 5 | MySQL 資料品質 | DATA_FAIL | 外資 24,921 筆、投信 7,782 筆不符；12 筆官方 `--` 被存成 0；自營與融資約 100 萬筆 NULL（另案，從匯入端修正） |
| 6 | 總報酬 | TOTAL_RETURN_BLOCKED | 現金股利與權利拆分；Native7 與 Track B 價格基礎統一；g4 集中度定義 |
| 7 | 重新訓練 | `retraining-spec-v1` 仍是 `DRAFT_PENDING_OWNER_APPROVAL` | Owner 簽核規格後才能訓練；7 個舊模型維持 MODEL_BLOCKED |
| 8 | 投資有效性 | 未證明 | 沒有任何策略符合資格；需要獨立樣本、完整認證與統計門檻 |
| 9 | 邊界測試的明確例外 | 設計備註 | 是否把共用特徵核心搬到中性模組（之後的架構決定） |
| 10 | 其他 | 備註 | `.gitignore` 有一行原本就存在的誤改（`openspec/  changes/archive/` 多了空格）未動；worktree `Stock_Linbotv1.worktrees/od29` 還在，裡面的 `outputs`、`artifacts`、`data` 是目錄連結，要先用 `rmdir` 拆連結再移除 worktree |

## 7. 沒做的事

沒有重跑 22 個策略、384 組設定，沒有訓練 AI，沒有新增官方請求，沒有寫入 MySQL，沒有 push；Broker 維持 DISABLED。
