# 22 策略雙軌驗證：封存前補強報告

日期：2026-10-24。證據目錄：`outputs/dual_track_validation/2026-10-23/closure/`（下文簡稱 `CL/`）。本輪沒有重跑 22 策略、沒有訓練模型、沒有新增官方請求、沒有寫入 MySQL、沒有 push；Broker 維持 DISABLED。

**結論**：`2026-10-23-validate-22-strategies-dual-track` **目前不符合封存條件**，有 4 項需要 Owner 簽核或決定（第 6 節）。我沒有封存它。

## 1. 測試隔離（Task 1）

| 項目 | 結果 |
|---|---|
| 根因 | `test/test_run_daily_persistence.py` 以非 dry-run 呼叫 `run_daily_for_date('2026-04-28')`，沒有替換 `run_fundamental_production`，所以每次完整回歸都把正式狀態檔改寫成 asof 2026-04-28 的測試版本 |
| 修復 | `test/conftest.py` 新增兩個全域 fixture：（a）攔截 `fundamental_production._write_status`，只要目標是正式資料夾，就改寫到該測試的 `tmp_path`；（b）整個測試階段開始與結束時比對正式資料夾每個檔案的大小與修改時間，有任何變動就讓測試階段失敗。因為這個寫入函式在呼叫時才解析預設路徑，一個攔截點涵蓋所有測試 |
| 新測試 | `test/test_production_status_isolation.py`：指向正式資料夾的寫入落在暫存目錄、正式檔不變；寫到其他資料夾不受影響 |
| 完整測試前後記錄 | `python -m pytest test`（`scripts/test_isolation_record.py`，記錄在 `CL/test_isolation_record.json`）：正式資料夾所有檔案的大小、修改時間、SHA-256 **前後完全相同**，`changed_files = []`；狀態檔路徑 `outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/fundamental_production_status.json`，SHA-256 `bb561a27c5559ea2…`，修改時間不變 |
| 其他可能寫入的測試 | 原始碼掃描列出 9 個接觸這條路徑的測試檔（`test_backfill_pipeline`、`test_cli_dry_run`、`test_daily_selection_recovery`、`test_run_daily_persistence`、兩個 characterization 與 3 個 unit/runtime）：經檢視，其餘都已替換寫入函式、使用 dry-run 或自己的暫存目錄。這個攔截只涵蓋 `_write_status`；其他寫入正式資料夾的路徑（例如 shadow run、Ledger）靠第（b）項的整體比對偵測，完整測試沒有偵測到任何變動 |
| 正式狀態檔目前是不是測試資料 | **是**。我把修好後的測試輸出導向暫存目錄，再與正式檔比對：兩者 SHA-256 完全相同（`bb561a27…`），內容就是測試寫出的版本（asof 2026-04-28）。排程在 2026-10-23 18:34 寫入的正式版本（`53e8810b…`，asof 2026-10-22）已被測試覆蓋，沒有保留備份，無法還原 |
| 是否恢復 | **沒有**。依規定未經 Owner 核准不得覆寫或恢復；今晚 18:30（+08）的 `evening` 排程會以當天資料重新寫入 |

本輪以前的兩次改寫（03:57、15:13）都是測試造成。正式檔的修改時間至今仍是 2026-10-24 15:13:09（修復前的最後一次改寫），與 `CL/test_isolation_record.json` 記錄的值相同；修復之後所有包含 `test_run_daily_persistence.py` 的測試（完整測試兩次、單檔、封存前針對性測試）都沒有再動它。

## 2. MOPS 200 份抽樣（Task 2）

`scripts/mops_sample_check.py`，只讀既有原始快取（沒有網路請求）。抽樣：固定種子 20261024，每個（年度、季度）層抽 3 份，再用隨機補滿 200 份；母體 38,614 份申報、827 檔股票。

| 驗證項目 | 作法 | 結果（200 份） |
|---|---|---|
| 原始來源與雜湊 | 快取檔名是請求鍵的 SHA-256（重算比對）；中繼資料分類必須是 `REAL_DATA_PAGE`；記錄每份 HTML 與中繼檔的 SHA-256，以及所有讀取的財報快取檔雜湊 | 200 份全部符合 |
| 公告時間 | 用專案原本的解析器重新解析 HTML：公告日、公告時間戳、檔名 | 200 份全部相同 |
| 實際可得時間 | 用同一個交易日曆重算 `available_date` 與規則，並檢查不早於公告、同日可得只限 13:30 前公告 | 200 份全部相同；其中 148 份公告早於研究日曆起點（2023-01-03），全部被日曆下限統一成 2023-01-03（保守，不是歷史上真實的可得日） |
| 財報數值 | 與 t163sb04 快取逐值重算：營收、營業利益、EPS 原始值與累計轉單季；營業利益率與 yoy 衍生值 | 1,335 個數值中 1,329 個相同，**6 個不符（3 份申報）** |
| 修訂版本 | 資料集內同一期間的 `revision_id` 數；單季頁與批次頁中同一期間的申報列數 | 資料集 0 個期間有多個版本；原始頁面也沒有任何期間有多筆申報（沒有被丟掉的修訂） |
| 訊號日當時是否可取得 | 沒有獨立的「回放訊號日」測試。實際檢查的是：重算的可得日與儲存值相同、可得日不早於公告日、同日可得只限 13:30 前公告；因此收盤後公布的申報要到下一個交易日才可用。CSV 的 `usable_on_signal_date` 欄只是資訊欄（算式恆為真），**不是檢查** | 上述 3 項 200 份全部符合；「用每個訊號日回放資料集」這件事本輪沒有做 |

**逐筆結果**：`PASS` **197**、`FAIL` **3**、`BLOCKED` 0（`CL/mops_sample_200.csv`）。涵蓋 2013～2025 年度、4 個季度、174 檔股票。

**3 份 FAIL 是真的資料缺陷**（3338 的 2014Q4、4557 的 2016Q4、6606 的 2023Q3）：
- 它們的 `revenue_yoy` 與 `operating_income_yoy` 錯誤，例如 3338 的 revenue_yoy 存 −75.6%，重算應為 −15.2%。
- 原因在 `core/research/fundamental_pit_v3.py` 的 `add_derived_metrics`：當年四季完整所以是單季值（STANDALONE），但前一年同季的季別鏈不完整，維持累計值（CUMULATIVE），程式沒檢查口徑是否相同就直接相除。
- 全量掃描（`CL/mops_population_basis_scan.json`）：`revenue_yoy` 466／34,696 筆（1.34%）、`operating_income_yoy` 468／34,717 筆（1.35%）口徑不同；累計版 yoy 另有各 5 筆。抽樣的 3／200（1.5%）與此一致。
- **影響**：G2 使用 `operating_income_yoy`，所以受影響；G3 使用 `eps_yoy`，EPS 一律是累計值，不受這個缺陷影響。
- 我沒有改寫資料集，也沒有修改程式；修法（要求同口徑，或用快取重算前一年單季值）需要 Owner 核准並升版資料集。

**不能由 200 份通過就認證全部財報**：
- 200 份抽樣只能證明抽中的那些與快取一致；這次的缺陷就是抽樣第一次才發現的，其餘約 38,400 份沒有逐份驗證。
- 財報數值來自 2026-09-15 對 t163sb04 的**單次**抓取（最新數字）；首次公告時的數值與日後重編的數值無法從快取還原。
- 所以 G2/G3 的 DATA 仍是 `DATA_BLOCKED`；Native7 的 `quality_*` 策略用的是 MySQL 財報表，不在這次抽樣範圍內。
- 策略矩陣的更新放在 `CL/strategy_status_matrix_closure.csv`（原矩陣沒動）：沒有任何評等改變，只有 G2/G3 三列的說明、阻擋原因與解除條件加入上述發現。

## 3. 官方請求節流（Task 3）

詳見 `CL/request_spacing_exception_report.md`。

- **違規**：MI_MARGN 2022-10-07 → T86 2022-10-11 的記錄間隔 2.934 秒（要求 3.2 秒）；其餘 1,874 個間隔最小 3.177 秒；1,876 次請求都得到正常回應，沒有任何限速。
- **根因（機制已確認、單次停頓未證明）**：ledger 的 `RELEASED` 時間是閘門放行**之後**才取的牆上時鐘（含開檔延遲）；閘門本身用單調時鐘保證 3.2 秒，但舊 ledger 沒有記錄閘門自己量到的間隔。記錄間隔 ＝ 3.2 ＋ 本筆與上一筆記錄延遲的差；實測離群值上下對稱（最小 −0.266、最大 +0.386 秒）。但我在閒置機器上量到的寫入延遲最大只有 3.6 毫秒，無法解釋 266 毫秒，所以這個單次停頓的原因（記錄延遲或時鐘調整）無法證明，這一次放行的實際間隔不可考。
- **修復（只影響未來請求）**：`RequestGate` 新增 `last_gap`（閘門自己量到的間隔）、`last_release_wall` 與選用的 `state_path`（同一狀態檔的所有程序以獨占檔案鎖串行放行）；`LoggedGate` 的每筆 `RELEASED` 新增 `gate_gap_seconds`。不指定 `state_path` 時行為不變。
- **測試**：單程序、3 次重試每次相隔 ≥ 3.2 秒、3 個真實程序共用狀態檔相隔 ≥ 設定值（容許 0.02 秒＝Windows 時鐘刻度）；反向檢查（各程序用不同狀態檔）最小間隔是 0 秒，所以測試有偵測能力。
- **處置**：歷史 ledger 沒有修改、門檻沒有降低、原始 `verification.json` 仍是 **FAIL**。無法消除歷史違規，已產出例外處置報告，**等待 Owner 在 A（接受為記錄誤差）、B（重取相鄰兩日）、C（永久保留 FAIL）中簽核**。

## 4. OpenSpec 數量（Task 4）

`CL/openspec_count_reconciliation.json`：
- 先前說的 85／83（以及更早的 84／82）是用 `ls openspec/specs | wc -l` 數出的**項目數**：**83 個規格資料夾 ＋ 2 個一般 `.md` 檔案**（`frontend-design.md`、`webapp-testing.md`，不是規格）。
- 嚴格驗證實際檢查 83 個，全部通過；沒有任何規格資料夾漏掉驗證，也沒有驗證了卻沒有資料夾的項目。
- 差異是非規格檔案，不是遺漏；沒有刪除任何東西。我先前把 85 稱為「資料夾」是措辭錯誤，在此更正。

## 5. 驗證與測試

| 項目 | 結果 |
|---|---|
| 封存前針對性測試（`CL/verification_closure.json`） | 182 passed，0 failed |
| 完整測試 `python -m pytest test`（修復測試隔離之後） | 1,266 passed、3 failed、4 skipped、4 xfailed |
| 3 個失敗 | 見下 |
| 原始 `verification.json` | 保留 FAIL（`failed_checks = [REQUEST_SPACING]`），SHA-256 記在 `verification_closure.json` |
| 模型、封存、保護來源、Frozen ledger、runner | 與基準相同，沒有新增雜湊例外 |
| 本輪核心程式變更 | 只有 `core/research/sources/twse.py`（`RequestGate`，向下相容） |

完整測試的 3 個失敗：
1. `test_openspec_change_artifacts_are_not_git_tracked`：**我造成的**。上一輪的證據提交用 `git add -f` 把本變更的 4 個 OpenSpec 文件加進 Git，違反「OpenSpec 只留在本機」的規定。已用 `git rm --cached` 移出索引（檔案仍在本機），該測試現在通過。
2. `test_backtest_supplement_adds_eps_when_op_margin_already_exists`：`BacktestEngine` 以 `__new__` 建立（沒有執行 `__init__`）時，`find_candidates` 讀 `self.research_data` 會拋 `AttributeError`。在 `f35b7e1`（Native7 研究快照那一輪）之前這個測試是通過的。
3. `test_research_package_has_no_prohibited_imports`：`core/research/native_frame.py` 匯入 `core.calc_indicators`（共用特徵核心），違反 `core.research` 的匯入邊界。同樣在 `f35b7e1` 之前通過。

第 2、3 項**不是本輪造成**（我用 `f35b7e1~1` 的暫時 worktree 確認過前者與後者當時都通過），但先前的回歸清單沒有包含這兩個測試，所以一直沒被發現。它們都要改 production 核心檔（`core/backtest/runner.py` 或 `core/research/native_frame.py`），而 `runner.py` 的雜湊正被 OD-29 的 characterization 綁定，所以我沒有修改，等 Owner 決定。

## 6. 是否符合封存條件

**不符合。** 需要 Owner 簽核或決定的項目：

| # | 項目 | 需要的決定 |
|---|---|---|
| 1 | 請求間隔 2.934 秒（`verification.json` 仍 FAIL） | 在 A／B／C 中簽核；沒有簽核前不能改判 PASS |
| 2 | MOPS：3／200 抽樣失敗；全量有 466＋468 筆 yoy 口徑混用 | 是否核准修正 `add_derived_metrics` 並升版資料集；是否接受 G2/G3 維持 DATA_BLOCKED 封存，還是全量驗證後再封存 |
| 3 | 正式狀態檔現在是測試資料 | 是否等今晚 18:30 排程自然覆寫（建議），或是否另行核准手動處理 |
| 4 | 2 個既有測試失敗（runner 屬性、核心匯入邊界） | 是否核准修改 production 核心檔（`runner.py` 用 `getattr`；或移動共用特徵核心的位置），修改後 OD-29 的 characterization 要重新綁定 |

另外，tasks.md 目前有 2 項未勾選（第 8 階段的簽核項），這些都是等 Owner 決定，不是漏做。
