# 歷史雜湊與正式狀態檔完整性報告

產生時間：2026-10-24 04:10（+08:00）。對應變更：`2026-10-23-validate-22-strategies-dual-track`（含 OD-29）。

## 1. 歷史雜湊與保護檔

| 檢查 | 結果 | 證據 |
|---|---|---|
| 7 個舊 AI 模型 | SHA-256 不變；全部 `MODEL_BLOCKED` | `baseline.json`、`od29_valuation/baseline.json`、`verification.json` |
| 歷史封存（5,463 筆） | 0 筆變動（見第 2 節的說明） | `od29_valuation/baseline.json`、`verification.json` |
| 保護原始碼（74 個） | 只有既有核准的 `core/strategies/growth_momentum_breakout.py`（P0-3） | 同上 |
| 先前回合封存輸出 | 不變 | `verification.json` |
| Frozen OOS Ledger | SHA-256 不變；Frozen cutoff 2026-07-28 | `verification.json` |
| 本輪程式變動 | 只在 `EXPECTED_CODE_CHANGES` 內（含 OD-29 的 `core/backtest/runner.py`） | `verification.json` |
| 雜湊例外 | 沒有新增任何例外 | `scripts/dual_track_validation.py` `KNOWN_EXCEPTIONS` |
| 官方請求間隔 | **FAIL**：1 處記錄間隔 2.934 秒（MI_MARGN 2022-10-07 → T86 2022-10-11，2026-10-23 20:57 +08:00）；門檻未放寬 | `verification.json` `request_spacing` |

## 2. 正式狀態檔 `fundamental_production_status.json`

路徑：`outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/fundamental_production_status.json`。

### 2.1 本輪觀察到的三個版本

| 時間（+08:00） | SHA-256 | 檔案內 `asof_date` | 寫入者 | 證據等級 |
|---|---|---|---|---|
| 歷史雜湊記錄時 | `bb561a27…` | 2026-04-28 | **回歸測試**（見 2.3） | PROVEN（內容與測試相符，見下） |
| 2026-10-23 18:34:02 | `53e8810b…` | 2026-10-22 | 正式排程 `evening` 的 `run_daily` 步驟（容器 `stock_ai_stock_bot`） | PROVEN（`integrity_event.json`：pipeline_runs 結束時間與檔案 mtime 相同到秒） |
| 2026-10-24 03:57:09 | `bb561a27…` | 2026-04-28 | **本輪的回歸測試**（`dual_track_validation.py tests`） | PROVEN（見 2.2） |

### 2.2 03:57 的改寫：由本輪測試造成

- 檔案 mtime 為 2026-10-24 03:57:09，落在 `python scripts/dual_track_validation.py tests`（pytest，約 03:53～03:57）執行期間；這段時間沒有正式排程。
- 改寫後的內容 `asof_date = 2026-04-28`，正是 `test/test_run_daily_persistence.py::test_run_daily_for_date_persists_every_persistence_strategy` 寫死的日期。
- 這個測試以非 dry-run 呼叫 `run_daily.run_daily_for_date('2026-04-28')`，但沒有替換 `run_fundamental_production`，所以 `FundamentalRuntimeAdapter.evaluate_production(write_status=True)` 會把正式狀態檔寫到 `OPERATION_ROOT`（工作區內的正式路徑）。測試的其他外部動作（DB、推薦落庫）都有替換，只有這一個沒有。
- 同一份測試在本輪較早的回歸（03:20 左右）以及先前每一輪回歸都會做同樣的事。

### 2.3 對「歷史雜湊不變」的意義

- 歷史雜湊清單記錄的 `bb561a27…` 本身就是這個測試寫出的版本（`asof_date = 2026-04-28`），**不是正式排程產生的紀錄**。
- 因此本輪 `historical_changed = []` 的原因是：測試又把檔案寫回同一個測試版本。**這不代表正式狀態保持不變**，也不能當成「所有歷史研究證據都未改變」的證明。這個檔案本質上是會被正式排程與測試輪流改寫的動態檔案。
- 正式排程寫入的版本（`53e8810b…`，asof 2026-10-22）已被本輪測試覆蓋；它的內容沒有另外保存，所以無法逐位元還原。今晚 18:30 的正式排程會依當天資料重新寫入。
- 兩個版本的 `production_eligibility` 都是 `BLOCKED`、`block_reason = INSUFFICIENT_OOS`、`FRESH_OOS_TRADING_DAYS = 53`；正式推薦與下單都不依賴這個檔案的 asof 欄位（Broker 維持 DISABLED）。在下一次正式排程之前，讀取這個檔案的畫面會顯示 asof 2026-04-28。

### 2.4 建議（未執行，需 Owner 核准）

1. 在 `test/test_run_daily_persistence.py` 加上 `monkeypatch.setattr(run_daily, 'run_fundamental_production', ...)`（與 `test/characterization/test_daily_selection_deterministic.py` 相同的寫法），讓測試不再寫入正式路徑。
2. 從歷史雜湊清單中把這個動態檔案移到「每次執行時另行記錄」的類別，並保存正式排程寫入的版本，而不是測試版本。這等於調整雜湊驗證的範圍，依規定需要 Owner 另行核准；本輪沒有自行修改。
3. 修正前，執行完整回歸後應記錄這個檔案的雜湊與 asof，作為事件紀錄。

## 3. 其他

- MySQL：本輪所有腳本只讀取快照或執行 SELECT；`verification.json` 以原始碼掃描確認沒有寫入路徑。
- 機密：Git 歷史中的 MySQL 明文密碼仍為 `SECURITY_ACTION_REQUIRED`；本輪沒有輸出、沒有更換帳密。
- 沒有模型訓練、沒有 recovered 正式切換、沒有 push；Broker `DISABLED`。
