# D3 因子前處理、每日歷史股票池與未來報酬標籤 — 設計訪談紀錄

> 建立日期：2026-08-06
> 狀態：**訪談已完成（D3-01～D3-10）；等待使用者確認後建立 OpenSpec change**
>
> **對應 change**：`d3-factor-preprocessing-and-forward-returns`（尚未建立）
> **前置**：`2026-07-30-factor-research-mvp`（已於 2026-08-06 archive，`openspec list` 無 active change）

---

## 1. 已確認事實（由 repo 直接查證，非決策）

### 1.1 與 D3 規格假設不符的發現

| 事實 | 證據 | 對 D3 的影響 |
|---|---|---|
| MVP change 已 archive，無 active change | `openspec/changes/archive/2026-08-06-2026-07-30-factor-research-mvp/`；`openspec list --json` = `[]` | 無重複 change 風險，直接建新 change |
| `listing_date` 在研究子系統完全不存在 | 僅 `core/mcp_client.py:93-94` 有無關欄位映射；MVP design §3.2 註明 OpenAPI `t187ap03_L` 有上市日期但無模組攝取 | 252 日上市門檻缺資料來源 → 決策 D3-01 |
| `add_is_tradable()` 恆為 False | `core/research/market_data.py:69-73`：排除 `quality_status ∈ {degraded, unverified}`；但 `normalize.py:36-47` 永遠寫 `unverified`，`ok` 從未被寫入 | 直接沿用會得到空 Universe → 決策 D3-02 |
| `rank_cs()` 回傳原始名次 1..N（ties=average、NaN keep），非 [0,1] 百分位 | `core/research/factor_operators.py:23-26`（`values.rank(axis=1)` pandas 預設） | `1 - rank_value` 公式需先百分位化 → 決策 D3-04 |
| 無可信 intraday 時間戳 | `market_closed_at` 由 `normalize.py:65` 恆填 `None`；`available_at` 已被 D1 刻意從 canonical 移除（MVP design §6.1「虛假精確」），派生版 `add_available_at()`（`market_data.py:76-94`）語意為「下一個已載入交易日的觀測時點」 | 時間洩漏檢查退到日期層級 → 決策 D3-07 |
| 工作區有 3 個未 commit 測試檔（+80 行，補強性質） | `test_factor_operators.py`（delta 測試）、`test_factors.py`（多資產隔離）、`test_pipeline.py` | 視為 494-passed 基準的一部分，D3 不碰 |

### 1.2 可沿用的 D1／D2 資產

| 資產 | 位置 | 語意 |
|---|---|---|
| `amount`（官方成交金額，NTD） | canonical 必要欄位；`normalize.py:64` 映射 MI_INDEX 成交金額 | Universe v2 流動性依據 |
| `liquidity_basis` | canonical 欄位；normalizer 永寫 `official_amount` | proxy 標記機制已存在 |
| `raw_close` | canonical；point-in-time 不可變 | Universe 價格門檻依據 |
| `adjusted_open`／`adjusted_close` | `normalize.apply_adjustments()`：`raw × Π(ex_date > t 的 event_factor)`；無事件股票 factor=1.0（adjusted=raw）；actions=None 時全 NaN + W007 | forward returns 依據 |
| `winsorize_cs(values, lower, upper)` | `factor_operators.py:35-43`；先 ±inf→NaN，逐日截面 quantile clip，截面 <2 個有效值 → 全 NaN | D3 前處理直接沿用 |
| `rank_cs(values)` | `factor_operators.py:23-26` | D3 前處理直接沿用 + 百分位化 |
| `FactorSpec.direction ∈ {1, -1, 0}` | `factors.py:24`；0 = UNDETERMINED 待 D4 定向 | D3 direction 調整依據 |
| D2 因子長表 schema | `artifacts.py:13-17`：`trade_date, stock_id, factor_name, factor_version, value, price_basis, run_id, asof_date, asset_id, factor_id, raw_value`（後四者為前者別名） | D3 輸入契約 |
| Universe v1 | `universe.py:8-25`：4 位代號、TWSE、`raw_close ≥ 10`、20 日均額 ≥ 2,000 萬（min_periods=1）、`volume > 0`；無 rule id、無版本 | v2 的基底；v1 不動 |
| 驗證框架 | `validation.py`：`Diagnostic(stage, code, severity, ...)`，severity 僅 FATAL/WARN 且硬編碼；pipeline 遇 FATAL 中止 | D3 leakage validator 沿用框架 |
| Pipeline 星型結構 | `pipeline.py` 為唯一 orchestrator；研究模組不得互相 import（`test_package_boundaries.py` 強制） | D3 新模組同樣只由 pipeline 呼叫 |
| 測試注入模式 | `RunConfig` 可注入 `quotes`/`actions` | D3 端到端離線測試沿用 |
| Archived design 的永久承諾 | X-7：universe 用 raw、因子/報酬用 adjusted 的不對稱**不得**「修成一致」；mask 於因子計算後套用；`execution_time = T+1` 明文預留給 forward-return 階段 | D3 全數遵守 |

---

## 2. 決策紀錄（ADR）

### D3-01 listing_date 來源：注入表 + 最小 t187ap03_L 抓取器

Universe v2 接受注入的 listing_date 表（`stock_id → listing_date`），離線測試用 fixture；同時依現有 sources 模式實作最小 TWSE OpenAPI `t187ap03_L` 抓取器供正式執行。缺 listing_date 的股票**保守排除**並記錄原因。
**理由**：不捏造上市日期；抓取器最小化避免把 D1 級資料契約工程拉進 D3 範圍。

### D3-02 is_tradable_t/t1 定義：沿用 volume>0 gate

`is_tradable_t` = 當日有行情列且 `volume > 0`（與 spec/research-universe 既有 gate 一致）。**不使用、不修改** `add_is_tradable()`；`quality_status` 恆為 `unverified` 的現況作為 D1 已知限制寫入 design。
**理由**：直接沿用 `add_is_tradable` 會得到空 Universe；修改它屬擴大修改 D1 契約，違反失敗規則。

### D3-03 D2→D3 銜接：in-process 單一 pipeline 執行

同一次 `pipeline.run()` 內先算 D2 因子，接著在記憶體內做 Universe v2 → 前處理 → forward returns → research dataset。`d2_source_run_id` = 本次 `run_id`。
**理由**：無 CSV round-trip；離線測試直接沿用注入模式；符合星型結構。

### D3-04 rank 百分位化：midrank (r − 0.5) / N

`rank_value = (rank_cs − 0.5) / N`（N = 當日截面有效資產數）。範圍 (0,1) 開區間，`1 − x` 反轉完全對稱。截面有效資產數 < 2 → NaN（與 `winsorize_cs` 既有行為對齊）。
**理由**：對 D4 Spearman Rank IC 而言任何單調轉換等價；midrank 讓 direction=-1 的分佈與正向同構且無除零。

### D3-05 direction=0：direction_adjusted_rank = NaN

`direction` 欄位本身（0）即為 UNDETERMINED 的可追蹤標記；`rank_value` 一律保留供 D4 雙向 Rank IC。不加額外狀態欄。

### D3-06 Universe v2 參數

`universe_rule_id = twse_research_v2`：`minimum_raw_close = 10`（沿用 v1）、`liquidity_window = 60`／`min_periods = 60`／`minimum_average_amount = 20,000,000 NTD`（額度沿用 v1、窗口改嚴）、`minimum_listing_trading_days = 252`、`allow_liquidity_proxy = false`（amount 缺值列視為窗口不完整，不補 0）。v1 規則與 `universe_counts` 輸出**完全不動**。

### D3-07 時間欄位：欄位保留、值為 NaT

`factor_asof_time / source_max_available_at / execution_time / entry_price_time` 欄位存在但值為 NaT；日期層級欄位（`factor_asof_date, source_max_trade_date, execution_date`）為實值。Leakage validator：日期層級檢查一律 FATAL；時間層級檢查只在欄位非空時執行。
**理由**：D1 已裁定精確發布時刻為虛假精確；schema 穩定讓未來有可信時戳時 D4 無需改表。

### D3-08 is_tradable_t1 只作標籤條件

Universe membership 只用 T 日及以前的資訊（無 lookahead）；T+1 不可交易 → forward return = NaN + `label_missing_reason = t1_untradable`，因子仍參與當日 winsorize/rank。
**理由**：T+1 資訊進 membership 會汙染 T 日截面，且與規格測試 14.1.9 矛盾。

### D3-09 Artifact：同 run 目錄加子目錄

`artifacts/factors/<run_id>/` 內新增 `universe_membership.csv`、`preprocessing_summary.csv`、`leakage_validation.csv`、`label_coverage.csv`、`research_dataset/<factor_id>/<year>.csv`；單一 `run_manifest.json` 擴充 D3 欄位。

### D3-10 OpenSpec deltas：MODIFY 1 + ADD 3

MODIFY `research-universe`（新增 v2 versioned rule 需求，v1 需求不動）；ADD `research-factor-preprocessing`、`research-forward-returns`、`research-dataset`。

### 附帶決議（推薦即採，未逐題訪談）

- 新模組：`core/research/factor_preprocess.py`、`forward_returns.py`、`research_dataset.py`；Universe v2 擴充現有 `universe.py`；leakage 檢查加在 `validation.py`（獨立於資料產生模組，由 pipeline 呼叫）。
- D3 階段為 opt-in：`RunConfig` 提供 `listing_dates`（及 universe v2 規則）時才執行，既有 D2 行為與測試不受影響。
- 行情載入窗口向後延伸 `max_horizon + 1 = 61` 個交易日以計算 60 日標籤；`requested_window` 語意仍為 asof 範圍。
- 缺失原因 vocab：`factor_missing_reason ∈ {not_listed, listing_history_insufficient, insufficient_lookback, not_in_universe, …}`；`label_missing_reason ∈ {t1_untradable, tail_insufficient, adjusted_open_unavailable, zero_denominator, …}`。
- forward return 公式：`adjusted_open[t+H+1] / adjusted_open[t+1] − 1`，H ∈ {1,5,10,20,60}；分母 ≤ 0 或缺值 → NaN；不跨停牌找替代進場日；不跨資產 shift。

---

## 3. 詞彙表（Glossary）

| 詞彙 | 定義 |
|---|---|
| **Universe v2（`twse_research_v2`）** | 版本化每日研究股票池：TWSE 4 位普通股、上市 ≥252 交易日、`raw_close ≥ 10`、60 日均成交額 ≥ 2,000 萬（完整窗口）、T 日可交易。只用 point-in-time raw 資料。 |
| **listing_age_trading_days** | 官方 `listing_date` 到 `asof_date` 的交易日數（以交易日曆計，非 DataFrame 列數）。 |
| **available_history_count** | 系統實際持有的有效行情筆數。與上市歷史分開判定，不得互相推論。 |
| **listing_history_sufficient** | `listing_age_trading_days ≥ 252`。是 membership gate。 |
| **factor_history_sufficient** | 該因子自身 lookback 的資料是否足夠。**不是** membership gate；不足時該因子值為 NaN。 |
| **is_tradable_t / is_tradable_t1** | 當日／次日「有行情列且 volume > 0」。t1 只影響標籤，不影響 membership。 |
| **winsorized_value** | 同一 `asof_date × factor_id` 截面、僅 Universe 成員、忽略 NaN 的 quantile clip（沿用 `winsorize_cs`）。 |
| **rank_value** | 同日截面 midrank 百分位 `(rank_cs − 0.5) / N`，範圍 (0,1)，ties=average。 |
| **direction_adjusted_rank** | direction=1 → rank_value；direction=−1 → 1 − rank_value；direction=0 → NaN（UNDETERMINED）。 |
| **forward_return_Hd** | `adjusted_open[t+H+1] / adjusted_open[t+1] − 1`：T 日收盤訊號、T+1 開盤進場、持有 H 個完整交易日、T+H+1 開盤出場。open-to-open，local_adjusted。 |
| **entry_lag** | 訊號日到進場日的交易日差，固定 = 1。 |
| **source_max_trade_date** | 該列因子所引用資料的最大交易日；FATAL 條件 `≤ factor_asof_date`。 |
| **execution_date** | T+1 交易日；FATAL 條件 `> factor_asof_date`。 |
| **label_missing_reason / factor_missing_reason** | 標籤／因子缺值的可追蹤原因碼（見附帶決議）。 |
| **research dataset** | 主鍵 `asof_date × asset_id × factor_id` 的長表，含因子四態值、Universe 欄位、時間欄位、五個 forward returns；無 Inf；直接供 D4 Rank IC。 |
