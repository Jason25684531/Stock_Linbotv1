# Tasks: 2026-07-30-factor-research-mvp

> **通則**
<!-- > - 一任務一 commit；**回復以 Phase 為單位**逆序 `git revert`，或以 merge commit `git revert -m 1`。**單一 task commit 不足以回復整個 change。** -->
> - 本 change **不修改任何既有業務程式**。若某任務發現必須改既有檔案，**停止並回報**，不得擴大範圍。
> - **市場範圍限定 TWSE 上市普通股。** 任何 TPEx／上櫃路徑皆為非目標。
> - 非目標（全 Phase 共用）：不改 `daily_market_data` schema、不改 `jobs/update_database.py`、不改 `services/mcp/server.py`、不改回測成交時點、不註冊排程、不新增 Python 依賴。
> - 本 change 的測試**不得呼叫 `StrategyManager`**（`docs/refactor/remaining_tech_debt.md` TD-01：會覆寫正式設定檔）。
> - **不得以即時外部 API 作為單元測試的必要條件。**
> - 日期為**預計開始日**；完成後不得修改原始日期，延期時保留原日期並加 `rescheduled` 註記。

---

## Phase 0：Pre-apply investigation（★ 唯讀調查，**apply 前完成**）

> **本 Phase 不撰寫任何程式碼**，只執行唯讀指令並記錄觀察結果。
> 因此它**可以在 apply 之前執行**——這解除了修訂 1 的循環（「未完成不得 apply，但不 apply 無法執行」）。
> 產出全部寫入 `_baseline/`。

- [x] [2026-07-30] 0.1 **P1** — 確定回補的歷史區間
  - **使用者已指定（2026-07-30）**：`requested_start = 2023-01-03`、`requested_end = 2026-07-28`（約 3.5 年）。
  - 理由：1 年區間對 `momentum_12_1` 的 253 日 lookback 幾乎沒有有效研究樣本。
  - 檔案：`_baseline/backfill_scope.md`
  - **完成條件**：載明區間、預估交易日數、預估 `MI_INDEX` 請求數、依保守節流參數的預估耗時。
  - Completed: 2026-07-30
  - Evidence: `_baseline/backfill_scope.md`
  - 需寫程式碼：**否**

- [x] [2026-07-30] 0.2 **P2** — 凍結既有測試 baseline 並記錄環境條件
  - 背景：多個測試的成敗取決於 MySQL 是否啟動（`core/db_helper.py:819` 拋 `ConnectionError`），非取決於程式碼。
  - 指令：`python openspec/changes/2026-07-30-factor-research-mvp/_baseline/scripts/p2_test_baseline.py`
  - **完成條件**：環境條件、完整失敗清單、計數皆記錄；同條件下兩次結果一致**且測試數 > 0**；`strategy_settings.json` sha256 前後比對已記錄。
  - Completed: 2026-07-30
  - Verification: 上述指令；原始輸出於 `_baseline/_p2_raw/pytest_run{1,2}.txt`
  - Evidence: `_baseline/test_baseline.md`
  - **凍結環境**：HEAD `a6daf10`、Python 3.11.9、pytest 9.0.2、**MySQL NOT REACHABLE**
  - **凍結結果**：兩輪皆為 `372 passed, 2 failed, 3 errors, 1 skipped, 1 xfailed`（約 16.5 分鐘／輪），`DETERMINISTIC = True`
  - **凍結的 5 項失敗**（B2 比對基準，**failed ∪ errors**）：
    1. `test/characterization/test_trade_sequence_regression.py::test_seeded_v31_trade_sequence_matches_the_characterization_baseline`
    2. `test/test_push_to_line_flex.py::test_run_evening_broadcasts_uniform_carousel`
    3. `test/characterization/test_atomic_replace_table.py::test_atomic_replace_swaps_full_content`
    4. `test/characterization/test_atomic_replace_table.py::test_atomic_replace_can_preserve_rows_matching_where`
    5. `test/characterization/test_atomic_replace_table.py::test_atomic_replace_failure_leaves_original_table_intact`
  - **5 項全部同源**：皆終止於 `core/db_helper.py:819` 的 `ConnectionError`（MySQL 未啟動）。**與程式碼缺陷無關。**
  - ⚠️ **`-rf` 不列 ERROR。** 3 個 error 的身分取自原始輸出的 `=== ERRORS ===` 區段。**B2 比對必須涵蓋 failed ∪ errors。**
  - ⚠️ **TD-01 實證確認**：執行後 `strategy_settings.json` sha256 改變（`f64e37c7…` → `c6826bf3…`）。已執行 `git checkout --` 還原。
  - 首次執行因腳本路徑錯誤而無效（收集到 0 個測試），已修正並重跑；紀錄見 `_baseline/test_baseline.md` §7。
  - 需寫程式碼：**否**

- [x] [2026-07-30] 0.3 **P3** — 查證 `MI_INDEX` 的回應分類
  - 背景：`stat != 'OK'` 不得一律當成非交易日（design §3.1）。
  - 指令：`python openspec/changes/2026-07-30-factor-research-mvp/_baseline/scripts/p3_p4_mi_index.py`
  - **完成條件**：各情境的 HTTP status、`stat`、頂層 keys、`tables` 數皆記錄且可互相區分；結果回填 `_baseline/source_capability.md` §2.2 與 design §3.1。
  - Completed: 2026-07-30
  - Verification: 上述指令；原始 JSON 於 `_baseline/_p3p4_raw/`
  - Evidence: `_baseline/source_capability.md` §2.2
  - **實測結論**：
    - **HTTP status 恆為 200**，不帶分類資訊 → 分類只能依 `stat`
    - 週末（`20260726`）與國定假日（`20260101`）**簽章完全相同** → `NON_TRADING_DAY` 判定可信
    - 未來日（`20991231`）與過早日（`19000101`）各有獨立 `stat`，頂層**只有 `stat`** → 屬**請求範圍錯誤**，非非交易日
    - **新事實：來源歷史下界 = 2004-02-11**（民國 93/2/11）
    - 分類由 4 類擴為 **5 類**（新增 `OUT_OF_RANGE`，`bound='early'` 為 **FATAL `F011`**）
  - 註：transport error／timeout／HTTP 非 200／JSON parse failure **無法以查詢日期誘發**，留給 tasks 2.2／2.3 的 mock 測試，**不得以日期回應冒充**。

- [x] [2026-07-30] 0.4 **P4** — 查證 `MI_INDEX` 整份 response 的 table 結構
  - **完成條件**：`tables` 總數、各 table 的 index／title／fields 數／rows 數皆記錄；確認「每日收盤行情」可由 title + 必要 fields 唯一定位，不依賴固定 index。
  - Completed: 2026-07-30
  - Verification: 同 0.3 的指令
  - Evidence: `_baseline/source_capability.md` §2.2b
  - **實測結論**：
    - 正常交易日共 **10 個 table**；目標位於 **index 8**——**既非首個亦非末個**
    - **index 9 是 rows=0／fields=0／title 為空字串的空 table** → 「取最後一個」或「取第一個有資料的」等啟發式**會失敗**
    - 「title 子字串 `每日收盤行情` ＋ 8 個必要欄位」→ 唯一匹配 `[8]`
    - 匹配數不為 1（**0 個或多個**）→ `F009_schema_drift`

> **✅ Phase 0 全部完成（P1／P2／P3／P4 皆已關閉，2026-07-30）。apply 的前置條件已滿足。**
> 其餘 Phase 皆為 implementation-time，不阻擋 apply。

---

## [2026-08-01] Phase 1：套件骨架、邊界與 ticker mapping

- [x] [2026-08-01] 1.1 建立 `core/research/` 套件骨架
  - 檔案：`core/research/__init__.py`、`core/research/sources/__init__.py`、`core/research/resources/__init__.py`
  - **驗收**：`python -c "import core.research"` 成功。
  - 測試：`test/unit/research/test_package_boundaries.py` — AST 掃描 `core/research/**/*.py` 的 import，斷言**不含** `flask`、`plotly`、`linebot`、`core.db_helper`、`core.strategies`、`core.calc_indicators`。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research/test_package_boundaries.py -q` → 2 passed
  - Evidence: `core/research/{__init__.py,sources/__init__.py,resources/__init__.py}`；`test/unit/research/test_package_boundaries.py`

- [x] [2026-08-01] 1.2 定義 provider 回傳契約
  - 檔案：`core/research/sources/__init__.py`
  - 目標：`RawResponse` frozen dataclass：`source, endpoint, request_parameters, retrieved_at, source_revision, payload, error`。
  - **驗收**：`frozen=True`；`error` 非 None 時 `payload` 必為 None。
  - 測試：`test/unit/research/test_raw_response.py`
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research/test_raw_response.py test/unit/research/test_package_boundaries.py -q` → 4 passed
  - Evidence: `core/research/sources/__init__.py`；`test/unit/research/test_raw_response.py`

- [x] [2026-08-02] 1.3 實作 ticker mapping（★ runtime 資料置於 `resources/`）
  - 檔案：`core/research/ticker_map.py`、**`core/research/resources/ticker_map.csv`**
  - 欄位：`stock_id, market, twse_code, yahoo_symbol, valid_from, valid_to, mapping_status`
  - **驗收**：
    1. `resolve('2330', <date>).yahoo_symbol == '2330.TW'`
    2. 查詢日不在 `[valid_from, valid_to]` → 回 `None`（呼叫端記 `W009`）
    3. **資料檔路徑解析到 `core/research/resources/`，不得解析到 `test/`**（以測試斷言路徑）
    4. **`core/research/` 內除 `ticker_map.py` 外，不得出現字串 `'.TW'` 或 `'.TWO'`**（原始碼掃描）
    5. `market='TPEx'` 的列可被資料模型表達，但**本 change 不由任何 adapter 消費**
  - 測試：`test/unit/research/test_ticker_map.py`
  - 註：`test/fixtures/research/` 可另放測試專用的 mapping 副本，**不得**被 runtime 讀取。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 9 passed
  - Evidence: `core/research/ticker_map.py`、`core/research/resources/ticker_map.csv`；`test/unit/research/test_ticker_map.py`

> Phase 1 completed: 2026-07-30. Verification: `python -c "import core.research"`、`python -m pytest test/unit/research -q` → 9 passed、`git diff --check`、`openspec validate 2026-07-30-factor-research-mvp --strict` → valid.

---

## [2026-08-03] Phase 2：TWSE adapter

- [x] [2026-08-03] 2.1 實作 `MI_INDEX` adapter
  - 檔案：`core/research/sources/twse.py`
  - 目標：`fetch_daily_quotes(trade_date, cache_dir) -> RawResponse`（design §3.1 M1）。
  - 必要行為：依**欄位名稱**定位；raw response 落地 `_raw/twse_rwd/MI_INDEX_<YYYYMMDD>.json`，命中即不重抓。
  - **驗收**：離線 fixture 測試通過；快取命中時零 HTTP 請求（monkeypatch 斷言）。
  - 測試：`test/unit/research/test_twse_adapter.py`
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research/test_twse_adapter.py -q` → 2 passed
  - Evidence: `core/research/sources/twse.py`；`test/unit/research/test_twse_adapter.py`

- [x] [2026-08-03] 2.2 ★ 回應五分類（Phase 0.3 已完成，實測依據見 `_baseline` §2.2）
  - 目標：`classify(response) -> TRADING_DAY | NON_TRADING_DAY | OUT_OF_RANGE(bound) | EMPTY_RESULT | SOURCE_ERROR`（design §3.1）。
  - **驗收（阻擋）**：
    1. 五類各有獨立 fixture 測試，fixture 取自 `_baseline/_p3p4_raw/` 的真實回應
    2. **HTTP status 不參與分類**（實測恆為 200）；分類僅依 `stat` 精確字串
    3. **未列於實測表的 `stat` 值歸為 `SOURCE_ERROR`，不得歸為 `NON_TRADING_DAY`**
    4. `NON_TRADING_DAY` **不計為警告**；`OUT_OF_RANGE` 與 `EMPTY_RESULT` **不重試**
    5. `OUT_OF_RANGE(bound='early')` → **FATAL `F011`**；`bound='future'` → `W013`
    6. `EMPTY_RESULT`（唯一匹配但列數為 0）→ `W010`；匹配數 0 或多個 → `F009`；`SOURCE_ERROR` → `W011` 且寫 `source_coverage.csv`
    7. transport error／timeout／HTTP 非 200／JSON parse failure 以 **mock** 測試，**不得以任何日期回應冒充**
  - 測試：同上
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 14 passed（離線 fixture 與 transport-error envelope）
  - Evidence: `core/research/sources/twse.py`；提前前置的純寫檔 `core/research/artifacts.py`；`test/unit/research/test_twse_adapter.py`、`test/fixtures/research/mi_index/`

- [x] [2026-08-03] 2.2b ★ 收盤行情 table 定位（Phase 0.4 已完成，依據見 `_baseline` §2.2b）
  - 目標：以 **title 子字串 ＋ 8 個必要欄位** 定位，匹配數必須恰為 1。
  - **驗收（阻擋）**：
    1. **原始碼中不得出現任何 table index 常數**（掃描斷言）——實測目標在 index 8，且 index 9 為空 table，任何位置啟發式都會錯
    2. 以真實 10-table fixture 驗證唯一匹配
    3. 匹配 **0 個** → `F009`；匹配 **多個** → `F009`（各有 fixture 測試）
  - Decision: 2026-07-30 使用者確認：匹配數 0 或多個皆為 `F009`；只有唯一匹配且列數為 0 才是 `W010`。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research/test_twse_adapter.py -q` → 7 passed
  - Evidence: `core/research/sources/twse.py`；`test/fixtures/research/mi_index/ten_tables.json`；`test/unit/research/test_twse_adapter.py`

- [x] [2026-08-04] 2.3 節流、重試與逾時
  - **驗收**：連續兩次請求間隔 ≥ 設定值（**注入假時鐘斷言，不得實際 `sleep`**）；重試耗盡回傳帶 `error` 的 `RawResponse`，**不拋例外**。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 18 passed
  - Evidence: `core/research/sources/twse.py`；`test/unit/research/test_twse_adapter.py`

- [x] [2026-08-04] 2.4 實作 `TWT49U` 除權息 adapter
  - 目標：`fetch_corporate_actions(start, end, cache_dir) -> RawResponse`（design §3.1 M2）。
  - **驗收**：民國中文日期（`112年01月04日`）正確轉 `2023-01-04`（含 `100年`、`115年` 邊界）；欄位缺失 → schema drift 標記。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 22 passed
  - Evidence: `core/research/sources/twse.py`；`test/unit/research/test_twse_adapter.py`

- [x] [2026-08-05] 2.5 TWSE OpenAPI 參考資料 adapter
  - 目標：`fetch_delisted()`、`fetch_holidays()`、`fetch_company_profile()`（design §3.2）。
  - **驗收**：**四種日期格式各有獨立測試**——`1150729`、`115/06/23`、`19620209`、`112年01月04日`。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 28 passed
  - Evidence: `core/research/sources/twse.py`；`test/unit/research/test_twse_adapter.py`

- [x] [2026-08-05] 2.6 Schema drift 偵測
  - **驗收**：刻意刪欄的 fixture 觸發 `F009`；新增未知欄的 fixture **不**觸發。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 29 passed
  - Evidence: `core/research/sources/twse.py`；`test/unit/research/test_twse_adapter.py`

> Phase 2 completed: 2026-07-30. Verification: `python -m pytest test/unit/research -q` → 29 passed；`git diff --check`；`openspec validate 2026-07-30-factor-research-mvp --strict` → valid.

---

## [2026-08-06] Phase 3：yfinance adapter（僅對帳）

- [x] [2026-08-06] 3.1 實作 adapter
  - 檔案：`core/research/sources/yahoo.py`
  - **驗收（阻擋）**：實際傳入的 kwargs **完全等於** `{auto_adjust: False, actions: True, keepna: True, repair: False, interval: '1d'}`，且**不含 `period`**（monkeypatch 斷言）。
  - 依據：實測預設 `auto_adjust=True`、`keepna=False`（`_baseline/source_capability.md` §3）。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 30 passed
  - Evidence: `core/research/sources/yahoo.py`；`test/unit/research/test_yahoo_adapter.py`

- [x] [2026-08-06] 3.2 來源 metadata
  - **驗收**：`ticker, package_version, request_parameters, requested_period, retrieved_at, repair_status, source_error` 齊全；`package_version` 讀自 `yfinance.__version__`（非硬編碼）。
  - Completed: 2026-07-30
  - Verification: `python -m pytest test/unit/research -q` → 31 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/sources/__init__.py`；`core/research/sources/yahoo.py`；`test/unit/research/test_yahoo_adapter.py`

- [x] [2026-08-07] 3.3 失敗隔離
  - **驗收（阻擋）**：yfinance **完全不可用時，canonical 與因子輸出完全不受影響**；`reconciliation_summary.csv` 仍產生（僅表頭），`reconciliation_coverage=0`。
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 32 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `test/unit/research/test_yahoo_adapter.py::test_fetch_history_isolates_an_unavailable_vendor`
  - Deferred end-to-end acceptance (user-approved 2026-07-31): Phase 9 pipeline/artifacts integration SHALL prove canonical/factor outputs unchanged, header-only `reconciliation_summary.csv`, and `reconciliation_coverage=0`.

---

## [2026-08-08] Phase 4：Normalization、Corporate actions、還原、對帳

- [x] [2026-08-08] 4.1 來源正規化
  - 檔案：`core/research/normalize.py`
  - **驗收**：`"1,673,263,794"` → `1673263794.0`；`"--"` → NaN，**不得為 0**。
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 34 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/normalize.py`；`test/unit/research/test_normalize.py`

- [x] [2026-08-08] 4.2 ★ 產生 `corporate_actions` 獨立契約
  - 目標：`ex_date, stock_id, action_type, pre_ex_close, ex_reference_price, event_factor, source, retrieved_at`（design §6.2）。
  - **驗收（阻擋）**：
    1. `event_factor = ex_reference_price / pre_ex_close`
    2. **契約中不得存在 `cash_dividend` 或 `stock_split_ratio`**（原始碼掃描）——來源只提供合併的「權值+息值」
    3. `(ex_date, stock_id)` 重複 → `F002`
  - 測試：`test/unit/research/test_corporate_actions.py`
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 37 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/normalize.py`；`test/unit/research/test_corporate_actions.py`

- [x] [2026-08-09] 4.3 Lineage 欄位填充
  - **驗收（阻擋）**：**同一列 OHLC 的四個價格必須來自同一來源的同一次回應**（斷言不存在混來源的列）。
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 38 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/normalize.py::quote_lineage`；`test/unit/research/test_normalize.py`

- [x] [2026-08-09] 4.4 還原係數計算
  - **驗收**：
    1. 無事件 → `adjustment_factor` 全為 1.0
    2. 單一事件 → 事件日**之前**為 `event_factor`，事件日**當日及之後**為 1.0
    3. `pre_ex_close <= 0` 的事件跳過並記 WARN，**不產生 Inf**
    4. `adjustment_source='unavailable'` 時 `adjusted_*` 為 NaN，**不得以 raw 冒充**
  - 測試：`test/unit/research/test_adjustment.py`（手算 fixture）
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 41 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/normalize.py::apply_adjustments`；`test/unit/research/test_adjustment.py`

- [x] [2026-08-10] 4.5 ★ Reconciliation
  - 檔案：`core/research/reconcile.py`
  - **驗收（阻擋）**：
    1. 差異超門檻 → `W004`，**不是 FATAL**
    2. **對帳前後 canonical DataFrame 完全相等**
    3. **固定層為「區間內平均 `amount` 前 20 檔」**，不得使用市值（本 pipeline 無市值資料）
    4. **抽樣種子為獨立的 `reconciliation_seed` 設定值**；斷言「相同 seed + 不同 `run_id` → 相同抽樣結果」
    5. 未抽樣的列 `quality_status='unverified'`，**不得為 `ok`**
  - 測試：`test/unit/research/test_reconciliation.py`
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 43 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/reconcile.py`；`test/unit/research/test_reconciliation.py`

---

## [2026-08-11] Phase 5：研究資料契約

- [x] [2026-08-11] 5.1 定義 `canonical_quotes` schema
  - 檔案：`core/research/market_data.py`（design §6.1）
  - **驗收（阻擋）**：
    1. 必填欄缺失 → `F001`；多出未知欄 → 通過
    2. **契約中不得存在 `cash_dividend`、`stock_split_ratio`、`available_at`**（原始碼掃描）
    3. 存在 `market_closed_at`、`retrieved_at`、`adjustment_as_of`、`liquidity_basis`、`quality_status`
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 45 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/market_data.py`；`test/unit/research/test_market_data.py`

- [x] [2026-08-12] 5.2 requested / loaded window
  - **驗收**：`maximum_lookback=253` 時 `loaded_start` 至少早於 `requested_start` 263 個交易日；輸出僅含 requested 區間。
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 46 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/market_data.py::loaded_window`；`test/unit/research/test_market_data.py`

- [x] [2026-08-12] 5.3 長 ↔ 寬轉換
  - **驗收（阻擋）**：
    1. pivot 前斷言 `(trade_date, stock_id)` 唯一；重複即 `F002`
    2. **禁用 `pivot_table`**（原始碼掃描該字串不存在於 `core/research/`）
    3. 各寬表 index 與 columns 完全一致；不符即 `F008`
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 47 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/market_data.py::to_wide`；`test/unit/research/test_market_data.py`

---

## [2026-08-13] Phase 6：D1 驗證

- [x] [2026-08-13] 6.1 FATAL 規則 `F001`–`F011`
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 49 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/validation.py`；`core/research/market_data.py`；`core/research/sources/twse.py`；`test/unit/research/test_validation.py`
  - **驗收**：每個代碼各有觸發與不觸發測試。**含 `F011_window_before_source_start`**（請求日期早於來源歷史下界 2004-02-11）。

- [x] [2026-08-13] 6.2 WARN 規則 `W001`–`W013`
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 50 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/validation.py`；`core/research/{normalize,reconcile,factors,sources/twse,ticker_map}.py`；`test/unit/research/test_validation.py`
  - **驗收**：每個代碼各有觸發測試；WARN 不中止。**含 `W010`／`W011`／`W012`／`W013`。**

- [x] [2026-08-14] 6.3 分級不可降級
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 51 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/validation.py::validate`；`test/unit/research/test_validation.py`
  - **驗收（阻擋）**：`validate()` 簽章中**不存在**可調整分級的參數（`inspect.signature` 斷言）。

- [x] [2026-08-14] 6.4 ★ Validation 只做契約層，不做 rolling
  - **驗收（阻擋）**：`validation.py` **不含任何 `.rolling(`**（AST 掃描）；其產生的診斷全部 `stage='contract'`；**不產生 `W012`**。
  - Completed: 2026-07-31
  - Verification: `python -m pytest test/unit/research -q` → 52 passed；`strategy_settings.json` SHA256 `f64e37c7…41ec` unchanged
  - Evidence: `core/research/validation.py`；`test/unit/research/test_validation.py`
  - 依據：design 決策 X-6。

- [ ] [2026-08-14] 6.5 Validation artifact（★ 由 pipeline 合併後寫出）
  - 欄位：**`stage, code, severity, trade_date, stock_id, detail`**（`stage` ∈ `contract` \| `factor`）
  - **驗收（阻擋）**：
    1. 零 WARN 時仍產生 `validation_report.csv`（僅表頭）；**不得僅 print**
    2. **`validation.py` 與 `factors.py` 皆不寫此檔**——由 `pipeline.py` 合併兩階段診斷後交給 `artifacts.py` 寫出
    3. 同時含 contract 與 factor 診斷的 run，兩者皆出現於同一份報表且 `stage` 正確

---

## [2026-08-15] Phase 7：每日動態股票池

- [ ] [2026-08-15] 7.1 universe mask（★ 一律使用**原始價**）
  - 檔案：`core/research/universe.py`
  - 規則：`^[1-9]\d{3}$` ∩ `market == 'TWSE'` ∩ **`raw_close >= 10`** ∩ `rolling_20_mean(amount) >= 20_000_000` ∩ `volume > 0`
  - 流動性 proxy 公式：**`raw_close × volume`**
  - **驗收（阻擋）**：
    1. `0050`、`00631L`、`006208` 皆被排除
    2. **`market='TPEx'` 的列不得成為成員**
    3. mask 的 index／columns 與價格寬表完全一致
    4. 流動性優先官方 `amount`；缺值時用 proxy 並標 `W006`；兩者皆無則 `liquidity_basis='unavailable'`
    5. **`universe.py` 不得讀取任何 `adjusted_*` 欄位**（AST／原始碼掃描）
    6. **歷史成員資格對 `adjustment_as_of` 不變**：以「同一份行情 + 兩份不同 corporate action 快照」建 mask，斷言先前交易日的成員完全相同
  - 依據：design 決策 X-7。`adjusted_close` 是回溯快照，用它建歷史股票池會讓**同一個歷史交易日的成員因未來的公司行動而改變**。價格門檻問的是「那天實際股價是否高於跳動單位失真區」，屬 point-in-time 事實。
  - 註：**raw／adjusted 的不對稱是刻意的**——可交易性判斷用 raw，報酬率計算用 adjusted。需寫入 12.1 的限制文件，避免被後人「修正」成一致。

- [ ] [2026-08-15] 7.2 Universe counts artifact
  - 目標：`universe_counts.csv`：`trade_date, count, liquidity_basis_official, liquidity_basis_proxy`
  - **驗收（阻擋）**：檔案必產生；`count` 序列單調遞增至最後一日時，輸出明確警告文字「存活者偏差未消除，Rank IC 為上限估計」。

---

## [2026-08-16] Phase 8：D2 因子引擎

- [ ] [2026-08-16] 8.1 ★ 算子（修正後的 NaN 語義）
  - 檔案：`core/research/factors.py`
  - 算子：`ts_mean, ts_std, ts_max, ts_min, ts_corr, delta, pct_change, wilder_atr`
  - **驗收（阻擋）**：
    1. rolling `min_periods` **等於完整窗長**
    2. **`ts_std` 對窗口已滿的常數序列回 `0`，不是 NaN**（★ 修正）
    3. **NaN 僅保留給：窗口未滿、`ts_corr` 任一邊離散度為 0、任何分母為 0**（★ 修正）
    4. 除以 0 產生的 `Inf` 一律轉 NaN
    5. `pct_change` 使用 `fill_method=None`
    6. **量能一律先經 `volume.where(volume > 0)`，在任何除法或 log 之前**（★ 統一）
    7. `wilder_atr`：**`TR(1) = high(1) - low(1)`**；**seed = 前 14 筆 TR 的簡單平均**；其後 Wilder 遞迴。與手算一致。**第一個非 NaN 值在第 14 根日線**
    8. 輸出 index／columns 與輸入完全相同
  - 測試：`test/unit/research/test_operators.py`

- [ ] [2026-08-16] 8.1b ★ `FactorResult` 與因子階段診斷
  - 目標：因子計算回傳 `FactorResult(values, diagnostics)`。`diagnostics` 收錄**只有算完才知道**的觀察，目前為 `W012_zero_dispersion`，每筆標 `stage='factor'`。
  - **驗收（阻擋）**：
    1. `factors.py` **不寫任何檔案**（AST 掃描：無 `open`／`to_csv`／`Path.write_*`）
    2. `validation.py` **不做任何 rolling 運算**（AST 掃描：無 `.rolling(`），因此不產生 `W012`
    3. 常數且窗口已滿的序列 → `diagnostics` 含該股該窗的 `W012`
  - 依據：design 決策 X-6。修訂 2 把 `W012` 放在 `validation.py`，但它要等 rolling 算完才知道，會迫使 validation 偷算因子。
  - 測試：`test/unit/research/test_factor_diagnostics.py`

- [ ] [2026-08-17] 8.2 `FactorSpec` 與 registry
  - 欄位：`name, version, family, fn, required_columns, lookback, direction, price_basis, unit, description`
  - **驗收**：`frozen=True`；**12 個因子 `direction == 0`**；無 class／Singleton／decorator 註冊（原始碼掃描）。

- [ ] [2026-08-17] 8.3 ★ 實作 12 個因子（正式使用 `local_adjusted`）
  - **價格型因子的正式輸出使用還原價**（design §7.3）；同時產出 raw 版本至 `qa/`。

    | # | 因子 | 公式（`close` 等為**還原價**寬表） | lookback | required_columns |
    |---|---|---|---|---|
    | 1 | `momentum_20d` | `close/close.shift(20) - 1` | 21 | adjusted_close |
    | 2 | `momentum_60d` | `close/close.shift(60) - 1` | 61 | adjusted_close |
    | 3 | `momentum_12_1` | `close.shift(21)/close.shift(252) - 1` | 253 | adjusted_close |
    | 4 | `near_high_252d` | `close/close.rolling(252).max() - 1` | 252 | adjusted_close |
    | 5 | `return_5d` | `close/close.shift(5) - 1` | 6 | adjusted_close |
    | 6 | `volume_ratio_20d` | `v/v.rolling(20).mean()`，`v = volume.where(volume>0)` | 20 | volume |
    | 7 | `price_volume_corr_20d` | `ret.rolling(20).corr(log(v).diff())`，`ret=close.pct_change(fill_method=None)`，`v=volume.where(volume>0)` | **21** ★ | adjusted_close, volume |
    | 8 | `range_position` | `(close - low)/(high - low)`，0～1，當日盤中 | 1 | adjusted_high/low/close |
    | 9 | `realized_vol_20d` | `close.pct_change(fill_method=None).rolling(20).std(ddof=1) * sqrt(252)` | 21 | adjusted_close |
    | 10 | `natr_14d` | `wilder_atr(14)/close`，**不乘 100** | **14** ★ | adjusted_high/low/close |
    | 11 | `amihud_20d` | `(abs(ret)/amount).rolling(20).mean()`，**不縮放**，`amount` 優先官方值 | 21 | adjusted_close, amount |
    | 12 | `overnight_gap_20d` | `(open/close.shift(1) - 1).rolling(20).mean()` | 21 | adjusted_open/close |

  - **驗收（阻擋）**：
    1. 每個因子在手算 fixture 上與人工計算值一致（`rtol=1e-9`）
    2. `price_volume_corr_20d` 的 `lookback == 21`（★ 修訂 1 誤植為 22）
    3. **`natr_14d` 的 `lookback == 14`**（★ 修訂 2 誤植為 15）——14 根日線即湊滿 14 筆 TR，`TR(2)` 所需的前收盤是 bar 1，已在窗內
    4. **`adjustment_source == 'unavailable'` 的股票，正式因子值為 NaN，不得以 raw 冒充**
    5. `volume_ratio_20d` 的 `price_basis == 'not_applicable'`
  - 測試：`test/unit/research/test_factors.py`

- [ ] [2026-08-18] 8.4 First-valid-date 邊界
  - **驗收（阻擋）**：每個因子的第一個非 NaN 值恰好出現在第 `lookback` 個交易日（1-indexed）。早一天或晚一天皆失敗。
  - 特別覆蓋：`natr_14d` → 第 **14** 根；`price_volume_corr_20d` → 第 **21** 根；`momentum_12_1` → 第 **253** 根。
  - 註：此測試即為 8.3 驗收第 2、3 項的執行處——`lookback` 宣告值與實際首個有效位置**必須一致**，任一方寫錯都會在此失敗。

---

## [2026-08-19] Phase 9：★ Pipeline orchestration、artifacts、CLI

- [ ] [2026-08-19] 9.1 ★ 實作 `pipeline.py`
  - 檔案：`core/research/pipeline.py`
  - 目標：`run(config) -> RunResult`。依 design §2 的順序協調全部階段。
  - **驗收（阻擋）**：
    1. **`pipeline.py` 是唯一含階段順序的模組**
    2. **各轉換模組不互相 import**（`ticker_map` 為唯一例外）——AST 掃描斷言
    3. FATAL 時**中止後續階段**，不寫因子 artifact，但仍寫 `validation_report.csv`
    4. 每個階段可用純記憶體輸入單獨呼叫，不需執行其他階段、不需網路
    5. ★ **合併 contract 階段與 factor 階段的 diagnostics**，保留各自 `stage` 標記，再交給 `artifacts.py`
  - 測試：`test/unit/research/test_pipeline.py`

- [ ] [2026-08-19] 9.2 ★ `artifacts.py` 純寫檔
  - **驗收（阻擋）**：`artifacts.py` **不得 import** `factors`、`universe`、`market_data`、`normalize`、`reconcile`、`sources`（AST 掃描）。

- [ ] [2026-08-20] 9.3 分區寫檔（雙軌）
  - 目標：正式 `values/<factor>/<year>.csv`；QA `qa/values_raw/<factor>/<year>.csv`。
  - 長表欄位：`trade_date, stock_id, factor_name, factor_version, value, price_basis, run_id`
  - **驗收（阻擋）**：
    1. **不產生單一巨型 CSV**
    2. 正式與 QA 路徑不重疊
    3. 逐因子計算後立即寫檔並釋放，不同時保留 12 張因子寬表

- [ ] [2026-08-20] 9.4 `run_manifest.json`
  - **驗收**：design §13.1 全部欄位存在，含 `market_scope`、`fallback_mode`、`reconciliation_seed`、`adjustment_as_of`、`universe_count_median`、`price_basis.{primary,qa}`；`run_id` 由外部傳入（計算模組內**不得** `datetime.now()`，原始碼掃描）。

- [ ] [2026-08-21] 9.5 FATAL 時的 manifest 語義
  - **驗收（阻擋）**：FATAL 時 `run_manifest.json` 不存在或 `status='failed'`。**留下 `status='success'` 即失敗。**

- [ ] [2026-08-21] 9.6 CLI 入口
  - 檔案：`jobs/run_factor_research.py`
  - 參數：`--start`、`--end`、`--run-id`、`--no-fetch`、`--reconciliation-seed`、`--allow-vendor-fallback`（預設關閉）
  - **驗收（阻擋）**：`--help` 成功；模組行數 < 100；**不含任何 pandas 運算**；唯一的研究呼叫為 `pipeline.run()`。

---

## [2026-08-22] Phase 10：Fixtures 與阻擋條件

- [ ] [2026-08-22] 10.1 離線 fixtures
  - 檔案：`test/fixtures/research/`
  - 內容：`MI_INDEX` 與 `TWT49U` 真實 raw response 截取；四種回應形態（依 Phase 0.3 實測）；TWSE OpenAPI 三個 endpoint 樣本；yfinance 樣本；**手算 golden CSV（3 檔 × 260 交易日）**；測試專用 ticker map 副本。
  - 手算 fixture 設計：一檔線性上漲（動量可手算）、一檔**常數序列**（驗證 `ts_std` 回 **0**、`ts_corr` 回 NaN）、一檔含缺值與零成交量。260 天覆蓋 `momentum_12_1`（253）邊界。
  - **驗收（阻擋）**：`pytest test/unit/research/ -q` 在**完全離線**環境全過。
  - 註：**明確不採用因子數值 golden 快照測試**（design §17 A11）。

阻擋條件總表（B1–B16）：

- [ ] [2026-08-22] 10.2 **B1** 新測試全過 — `python -m pytest test/unit/research/ -q`
- [ ] [2026-08-22] 10.3 **B2** 既有測試不新增失敗
  - 基準：`_baseline/test_baseline.md` §3 的 **5 項清單**（HEAD `a6daf10`、**MySQL NOT REACHABLE**）
  - 指令：`python -m pytest test/ -q -rf --timeout=120`
  - **比對範圍必須是 failed ∪ errors**（`-rf` 不列 ERROR，只看 FAILED 會漏掉 3 項）
  - 通過條件：失敗集合 ⊆ 基準清單。失敗項**減少**不算違反，但須註明環境已改變且不得作為新 baseline
  - 執行後檢查 `strategy_settings.json` 是否被污染（TD-01），若是則 `git checkout --` 還原
- [ ] [2026-08-23] 10.4 **B3** 輸出無 Inf — 正式與 QA 全部 CSV；NaN 比例寫入 manifest
- [ ] [2026-08-23] 10.5 **B4** look-ahead sentinel — 末 5 天改極端值，斷言倒數第 6 天（含）以前完全不變，`rtol=1e-9`／`atol=1e-12`。**最重要的單一測試**
- [ ] [2026-08-23] 10.6 **B5** 缺值不得被 `pct_change` 跨越 — `[10, NaN, 12]` → `[NaN, NaN, NaN]`
- [ ] [2026-08-23] 10.7 **B6** 零成交量不產生 Inf — `volume=0` 時三個量能因子皆 NaN
- [ ] [2026-08-24] 10.8 **B7** ★ `ts_std` 語義 — 窗口已滿的常數序列回 **0**；窗口未滿回 NaN；`ts_corr` 對零離散度回 NaN。並斷言 `W012` 來自 `stage='factor'`，非來自 `validation.py`
- [ ] [2026-08-24] 10.9 **B8** universe mask 不破壞因子歷史窗口 — 證明「先算因子再套 mask」與「先套 mask 再算因子」結果不同，且實作採前者
- [ ] [2026-08-24] 10.10 **B9** 主來源失敗不被靜默掩蓋 — `source_coverage.csv` 記錄 + `is_fallback=true` + manifest `warning_counts`，三者缺一即失敗
- [ ] [2026-08-24] 10.11 **B10** FATAL 不留成功 manifest — 見 9.5
- [ ] [2026-08-25] 10.12 **B11** schema drift 有測試 — 見 2.6
- [ ] [2026-08-25] 10.13 **B12** ticker mapping 有測試 — 見 1.3，含 runtime 路徑不在 `test/` 的斷言
- [ ] [2026-08-25] 10.14 **B13** Wilder ATR 與手算一致 — 含 `TR(1)` 與 seed 的定義
- [ ] [2026-08-25] 10.15 **B14** 相同輸入與版本產生相同 artifact schema — 同 fixture、同 `run_id`、同 `reconciliation_seed` 執行兩次結果相同
- [ ] [2026-08-26] 10.16 **B15** yfinance 參數不使用預設值 — 見 3.1
- [ ] [2026-08-26] 10.17 **B16** 測試不依賴即時外部 API — 斷網環境全過；monkeypatch 阻斷 `requests`／`yfinance` 後仍全過
- [ ] [2026-08-26] 10.18 **B17** ★ 模組邊界 — `artifacts.py` 無業務 import；轉換模組不互相 import；CLI 無 pandas 運算；`factors.py` 不寫檔；`validation.py` 無 `.rolling(`
- [ ] [2026-08-26] 10.19 **B18** ★ universe 不受還原影響 — `universe.py` 不讀 `adjusted_*`；兩份不同 `adjustment_as_of` 快照建出的歷史 mask 完全相同（見 7.1 驗收第 5、6 項）

---

## [2026-08-27] Phase 11：Shadow dry run

> 阻擋：Phase 0.1（P1）必須先完成。

- [ ] [2026-08-27] 11.1 小區間 dry run
  - 目標：以 P1 指定區間的**最後 3 個月**執行完整 pipeline。
  - **驗收**：產出完整 `artifacts/factors/<run_id>/`，含 manifest、五個報表、`values/` 與 `qa/`。`status='success'`。
  - 命令：`python jobs/run_factor_research.py --start <s> --end <e>`

- [ ] [2026-08-27] 11.2 ★ **I2** — 記錄每日入選檔數（**不阻擋**）
  - 目標：檢視 `universe_counts.csv`，將中位數寫入 manifest `universe_count_median`。
  - **驗收**：數字已記錄。**若中位數低於 400，記入 `docs/research/factor_data_limitations.md` 說明其對後續分位數分析的限制，但不中止本 change。**
  - 註：★ 修訂 2 變更——修訂 1 將此列為阻擋條件。檔數是**研究品質訊號**，非**正確性缺陷**；pipeline 在 300 檔下依然正確。以阻擋處理會讓 D1／D2 因為屬於 D3 的顧慮而無法交付。

- [ ] [2026-08-28] 11.3 對帳結果檢視
  - **驗收**：`reconciliation_summary.csv` 存在；記錄抽樣覆蓋率與差異分布。

- [ ] [2026-08-28] 11.4 ★ raw vs adjusted 差異檢視
  - 目標：比對 `values/`（`local_adjusted`）與 `qa/values_raw/`（`raw_unadjusted`）。
  - **驗收**：差異集中於除權息日附近；若出現與 corporate action 無關的大量差異，代表還原邏輯有誤，須回頭修 Phase 4.4。

- [ ] [2026-08-29] 11.5 全區間 dry run
  - **驗收**：完整區間執行成功；記錄總耗時、請求數、快取命中率。

---

## [2026-08-30] Phase 12：文件與收尾

- [ ] [2026-08-30] 12.1 已知限制文件
  - 檔案：`docs/research/factor_data_limitations.md`
  - 必須載明：
    1. **市場範圍限定 TWSE 上市**；上櫃不在本 pipeline
    2. **存活者偏差未消除，Rank IC 為上限估計**
    3. 減資事件未涵蓋於還原係數
    4. 對帳為**抽樣**，非全量
    5. 處置股分盤集合競價會扭曲當日價格，未處理
    6. 若使用 proxy 流動性，其為 `close × volume` 近似
    7. `adjusted_*` 為 as-of 快照，不同 `adjustment_as_of` 的 run 不可直接比較
    8. Phase 11.2 的每日檔數中位數與其對分位數分析的意涵
    9. ★ **raw／adjusted 的刻意不對稱**：股票池門檻用 `raw_close`（可交易性＝ point-in-time 事實），正式因子用 `adjusted_*`（報酬率＝需連續序列）。**此不對稱是設計決策，不是疏漏，不得被「修正」成一致**（design 決策 X-7）
  - **驗收**：九項全部存在。

- [ ] [2026-08-30] 12.2 更新技術債
  - 檔案：`docs/refactor/remaining_tech_debt.md`
  - 確認 TD-01（測試覆寫 `strategy_settings.json`）與 TD-02a/b/c（丟棄 amount、universe filter 行為與 docstring 相反、三套矛盾規則）皆已記錄且證據行號正確。

- [ ] [2026-08-30] 12.3 後續 change 清單
  - 檔案：`docs/research/factor_research_roadmap.md`
  - 內容：TPEx 支援、修正正式 ingestion 保留 amount、`factor_values` DB 表、D3 Rank IC。

- [ ] [2026-08-31] 12.4 ★ **I1** — DB 事實查證（需 MySQL，**不阻擋**）
  - 目標：`daily_market_data` 的最早／最晚 `trade_date`、交易日數、distinct `stock_id`、每年 distinct stock 數。
  - 檔案：`_baseline/db_facts.md`
  - **驗收**：四項數字皆有實測值；「每年 distinct stock 數」序列若單調遞增至今日，於 12.1 的限制文件中記載「下市歷史列已被清除，存活者偏差＝固定清單等級」。
  - 註：★ 修訂 2 變更——修訂 1 將此列為「Phase 0.3 且阻擋 Phase 5」，但本 change 的 pipeline **不讀取 `daily_market_data`**（資料直接來自 TWSE），故此查證只影響**揭露措辭**，不影響正確性，改列為 Phase 12 且不阻擋。

- [ ] [2026-08-31] 12.5 OpenSpec 收尾
  - **驗收**：`openspec validate 2026-07-30-factor-research-mvp --strict` 通過；全部 task 勾選並附完成日期、驗證命令與證據路徑；`git diff --check` 無問題。
