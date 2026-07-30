# Design: 2026-07-30-factor-research-mvp

> 建立日期：2026-07-30
> 最後修訂：2026-07-30（修訂 2）
> 依開發憲法第十九條，本檔為本 change 全部架構決策的**唯一**記錄處。**不另建 `docs/adr/`**（訪談 D-17）。
> 來源查證證據：`_baseline/source_capability.md`
> 前置訪談決策 D-01～D-18 見 `docs/research/d1-d2-grill-session.md`；本檔 §20 標註哪些被推翻。

---

## 1. 現況資料流

```
                       jobs/update_database.py :: run_price_update()
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
 TWSE RWD MI_INDEX            TPEx aftertrading            TWSE RWD T86 / TPEx 3insti
 (:114 date=&type=ALL)        (:255,:273,:315)             (:170,:362)
        │                               │                               │
        │  回應「每日收盤行情」table 16 欄，                              │
        │  其中「成交金額」「成交筆數」                                    │
        │  在 :139-155 被 **丟棄**                                       │
        ▼                               ▼                               ▼
        └───────────────► 7 欄 DataFrame ◄──────────────────────────────┘
                                        │
                          :620 正則 ^([1-9]\d{3}|00)  ← 保留全部 ETF
                                        ▼
                     core/db_helper.upsert_stock_data()  REPLACE INTO (:1227)
                                        ▼
                          ┌──────────────────────────┐
                          │   daily_market_data      │
                          │  無 amount / adjusted /  │
                          │  corporate action /      │
                          │  lineage / listing status│
                          └──────────────────────────┘
```

**三個致命特性**

1. 官方「成交金額」在來源回應中存在，卻在欄位挑選時被丟棄。
2. 無任何 lineage——一列資料無法回答「誰給的、何時給的、是不是回補的」。
3. `core/calc_indicators.py` 的指標一律 `fillna(0)`（`:190`、`:324`、`:429`）。對線上選股是安全預設，對研究是偽造資料。

---

## 2. 目標資料流

```
                    jobs/run_factor_research.py
                            │  僅解析參數，零業務邏輯
                            ▼
        ┌───────────────────────────────────────────────┐
        │        core/research/pipeline.py              │
        │        唯一知道執行順序的模組                    │
        └───────────────────────────────────────────────┘
             │
             │ ①  sources.twse: MI_INDEX 逐交易日 / TWT49U 區間 / OpenAPI 參考資料
             │    sources.yahoo: 僅對帳樣本（預設 official_only）
             │    → raw response 落地 _raw/，命中即不重抓
             ▼
             │ ②  normalize: 依欄位名稱取值 → canonical_quotes（僅 raw 價格）
             │                            → corporate_actions（獨立契約）
             ▼
             │ ③  normalize: event_factor → 累積 adjustment_factor → adjusted_*
             ▼
             │ ④  reconcile: TWSE vs yfinance 抽樣比對 → reconciliation_summary
             │    純比對，**不修改任何 canonical 值**
             ▼
             │ ⑤  validate_contract: **資料契約層**驗證
             │    FATAL 中止（不留成功 manifest）／WARN 收集為 diagnostics
             ▼
             │ ⑥  market_data: 長→寬，pivot 前斷言鍵唯一
             ▼
             │ ⑦  factors: 在**完整行情**（含暖機）上計算
             │    正式 = local_adjusted；QA 對照 = raw_unadjusted
             │    → 回傳 FactorResult(values, diagnostics)
             │      diagnostics 含只有算完才知道的觀察（如 W012 零離散度）
             ▼
             │ ⑧  universe: 建立每日 mask（**在因子算完之後**）
             ▼
             │ ⑨  pipeline: **合併** ⑤ 與 ⑦ 的 diagnostics（帶 stage 標記）
             ▼
             │ ⑩  artifacts: 純序列化與寫檔（不含流程控制）
             ▼
        artifacts/factors/<run_id>/
```

> **決策 X-6：診斷分兩階段產生，由 pipeline 合併（修訂 3）。**
> 修訂 2 把 `W012_zero_dispersion` 列入 `validation.py` 的規則表，但它**要等 rolling 標準差算完才知道**。若堅持由 validation 產生，validation 就得自己算 rolling——那等於在資料驗證模組裡偷做因子運算，破壞模組邊界，且同一段 rolling 會被算兩次。
> **解法**：`validation.py` 只做**資料契約層**檢查（欄位、鍵、OHLC 關係、跳空、成交量）；`factors.py` 回傳 `FactorResult(values, diagnostics)`，把只有算完才知道的觀察放進 `diagnostics`；`pipeline.py` 合併兩者，`artifacts.py` 仍只負責寫檔。
> `validation_report.csv` 因此新增 **`stage`** 欄（`contract` \| `factor`），讓每一筆診斷的來源明確。
> **被拒替代**：把 W012 從規格中刪除 → 零離散度是需要被揭露的訊號（靜止股 vs 停牌／跌停鎖死），刪掉等於放棄 §8.1 的揭露承諾。

---

## 3. Endpoint Mapping（經實測，詳見 `_baseline/source_capability.md`）

### 3.1 TWSE RWD — 歷史主來源

> RWD 不屬於 OpenAPI，未列於 swagger，屬**非公開契約**，故 schema drift 為 FATAL。

#### M1. 每日全市場收盤行情

| 項目 | 內容 |
|---|---|
| **用途** | 歷史日 OHLC、成交股數、**成交金額**、成交筆數 |
| **endpoint** | `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={YYYYMMDD}&type=ALL&response=json` |
| **實際欄位** | 回應 `tables[]` 中 `title` 含「每日收盤行情」者，`fields` = `["證券代號","證券名稱","成交股數","成交筆數","成交金額","開盤價","最高價","最低價","收盤價","漲跌(+/-)","漲跌價差","最後揭示買價","最後揭示買量","最後揭示賣價","最後揭示賣量","本益比"]` |
| **日期格式** | 請求 `YYYYMMDD`；回應 `date` 同格式；`title` 為民國年中文 |
| **單位** | 成交股數＝**股**；成交金額＝**新台幣元**；價格＝元。數值含千分位逗號 |
| **歷史能力** | **有**。實測 `20230103` → `stat=OK`、該 table 24,001 列；`20260728` → 31,046 列 |
| **列數語義** | 上述列數為**「每日收盤行情」該一個 table 的列數**，涵蓋全部有價證券（含 ETF、權證、特別股），**非上市普通股檔數**（約 1,000 檔），亦非整份 response 的列數 |
| **canonical mapping** | 證券代號→`stock_id`、開盤價→`raw_open`、最高價→`raw_high`、最低價→`raw_low`、收盤價→`raw_close`、成交股數→`volume`、成交金額→`amount`、成交筆數→`transaction_count` |
| **涵蓋市場** | **僅上市（TWSE）** |

**回應狀態分類（★ 修訂 4：P3 實測完成，由 4 類擴為 5 類）**

實測依據：`_baseline/source_capability.md` §2.2（2026-07-30）。

> **關鍵：HTTP 狀態碼恆為 200**，即使查詢完全無效。**HTTP status 不帶任何分類資訊**，分類只能依 `stat` 字串。

| 類別 | 判定依據（實測字串） | 可重試 | pipeline 行為 |
|---|---|---|---|
| `TRADING_DAY` | `stat == 'OK'` 且收盤行情 table 匹配數 == 1 且列數 > 0 | — | 正常處理 |
| `NON_TRADING_DAY` | `stat == '很抱歉，沒有符合條件的資料!'` | 否 | 跳過該日，**不計為警告** |
| `OUT_OF_RANGE` `bound='future'` | `stat == '查詢日期大於今日，請重新查詢!'` | **否** | 跳過該日 + **WARN** `W013_request_out_of_range` |
| `OUT_OF_RANGE` `bound='early'` | `stat == '查詢日期小於93年2月11日，請重新查詢!'` | **否** | **FATAL** `F011_window_before_source_start` |
| `EMPTY_RESULT` | `stat == 'OK'` 且收盤行情 table 唯一匹配、但列數 == 0 | 否 | **WARN** `W010_source_empty` |
| `SOURCE_ERROR` | **其他任何情形**：未列於上表的 `stat`、transport error、timeout、HTTP 非 200、JSON parse failure | **是** | **WARN** `W011_source_error` + `source_coverage.csv`；若 opt-in fallback 則標 `is_fallback` |

**實測支持的三項判斷**

1. **`NON_TRADING_DAY` 的簽章可信**：週末（`20260726`）與國定假日（`20260101`）**兩個獨立情境給出完全相同的 `stat` 與頂層 keys**，可安全作為判定依據。
2. **未來／過早日期不是非交易日**：兩者各有語義明確的獨立 `stat`，且頂層**只有 `stat`**（連 `type` 都沒有）。它們是**請求範圍錯誤**，重試無用。
3. **來源歷史下界 = 2004-02-11**（由 `'查詢日期小於93年2月11日'` 得知）。目前 `loaded_start`（約 2021-11）距此尚有 17 年餘裕。

> **決策 X-8：`OUT_OF_RANGE(early)` 為 FATAL，不是 WARN。**
> 它代表 `loaded_start` 早於來源涵蓋範圍。若僅記 WARN 並跳過，暖機區間會**不完整卻看起來正常**——`momentum_12_1`（253 日）等長 lookback 因子會產出「有值但錯」的結果。依 §10 的界線（「會讓後續運算靜默算錯 → FATAL」），必須 FATAL。
> **被拒替代**：歸入 `SOURCE_ERROR` → 會觸發重試（浪費且無用），且 run 會帶著不完整暖機繼續完成。

> **`stat` 以精確字串比對，失效方向刻意偏保守。** TWSE 若改寫文案，比對失敗 → 落入 `SOURCE_ERROR`，**不會**被誤判為非交易日。寧可多報一次來源錯誤，不可少報一天真實資料。

**錯誤處理**：收盤行情 table 匹配數 **不為 1**（0 個或多個）→ `F009_schema_drift`；`fields` 缺任一宣告的來源欄 → `F009`。

**★ table 定位（P4 實測，見 `_baseline` §2.2b）**

正常交易日共 **10 個 table**，目標「每日收盤行情(全部)」位於 **index 8**——**既非首個亦非末個**，且 **index 9 是 rows=0／fields=0／title 為空字串的空 table**。

因此：

- **禁止硬編碼 index**
- **禁止**「取第一個 / 取最後一個 / 取第一個有資料的」等啟發式（index 9 會讓後者失效）
- 必須以 **title 子字串 `每日收盤行情` ＋ 8 個必要欄位全部存在** 比對，且匹配數必須恰為 1

#### M2. 除權除息計算結果（官方還原係數）

| 項目 | 內容 |
|---|---|
| **用途** | 歷史 corporate action 與**官方**還原係數 |
| **endpoint** | `https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={YYYYMMDD}&endDate={YYYYMMDD}&response=json` |
| **實際欄位** | `["資料日期","股票代號","股票名稱","除權息前收盤價","除權息參考價","權值+息值","權/息","漲停價格","跌停價格","開盤競價基準","減除股利參考價","詳細資料","最近一次申報資料 季別/日期","最近一次申報每股 (單位)淨值","最近一次申報每股 (單位)盈餘"]` |
| **日期格式** | 請求 `YYYYMMDD`；回應「資料日期」為**民國年中文**（`112年01月04日`） |
| **歷史能力** | **有**。實測 2023 全年 → `stat=OK`、1,086 列 |
| **canonical mapping** | 資料日期→`ex_date`、股票代號→`stock_id`、除權息前收盤價→`pre_ex_close`、除權息參考價→`ex_reference_price`、權/息→`action_type` |
| **★ 不提供** | **分離的現金股利與配股率**。只有合併的「權值+息值」與類別「權/息」 |
| **涵蓋市場** | **僅上市（TWSE）** |

### 3.2 TWSE OpenAPI — 參考資料（**無歷史、無參數**）

| # | 用途 | endpoint | 欄位（實測） | 日期格式 | 列數 |
|---|---|---|---|---|---|
| O1 | 終止上市名單 | `/v1/company/suspendListingCsvAndHtml` | `DelistingDate, Company, Code` | `115/06/23` | 264 |
| O2 | 交易日曆（休市日） | `/v1/holidaySchedule/holidaySchedule` | `Name, Date, Weekday, Description` | `1150101` | 27（**僅當年**） |
| O3 | 上市公司基本資料 | `/v1/opendata/t187ap03_L` | 中文欄名，含 `公司代號, 公司簡稱, 產業別, 上市日期` | `19620209`（西元） | 1,092 |

> **四種日期格式並存**：`1150729`（民國無分隔）、`115/06/23`（民國含斜線）、`19620209`（西元無分隔）、`112年01月04日`（民國中文）。每個 adapter 必須各自宣告並個別測試。

### 3.3 明確不使用

| endpoint | 理由 |
|---|---|
| `/v1/exchangeReport/STOCK_DAY_ALL` | 僅最新交易日，無參數 |
| `/v1/exchangeReport/TWT48U_ALL` | 除權除息**預告**表，非歷史 |
| `/v1/exchangeReport/FMSRFK_ALL` | 月彙總，無日 OHLC |
| `.../STOCK_DAY?stockNo=` | 逐股逐月，請求數約為 `MI_INDEX` 的 50 倍 |
| **TPEx 任何 endpoint** | **本 change 範圍限定 TWSE**（見 §5.0） |

### 3.4 yfinance — 僅對帳

| 項目 | 內容 |
|---|---|
| **版本** | `yfinance==0.2.66`（已釘選） |
| **呼叫參數** | **必須明確指定** `auto_adjust=False, actions=True, keepna=True, repair=False, interval='1d'`，並使用明確 `start`／`end`（不用 `period`） |
| **預設值危害（實測）** | `auto_adjust=True`（**預設回傳還原價**）、`keepna=False`（**預設丟棄缺值列**）、`raise_errors=False`（失敗靜默） |
| **取得欄位** | `Open, High, Low, Close, Adj Close, Volume, Dividends, Stock Splits` |
| **符號** | 由 `ticker_map` 提供。**禁止任何模組自行拼接 `.TW`／`.TWO`** |
| **必須保存** | `ticker, package_version, request_parameters, requested_period, retrieved_at, repair_status, source_error` |
| **角色** | **僅**寫入 `reconciliation_summary.csv`。`Adj Close` **永不**寫入 canonical |

---

## 4. 模組責任

| 模組 | 負責 | **不**負責 |
|---|---|---|
| `jobs/run_factor_research.py` | 參數解析、產生 `run_id`、呼叫 `pipeline.run(config)` | 一切業務邏輯 |
| **`pipeline.py`** | **執行順序、階段間資料傳遞、FATAL 中止、diagnostics 合併、manifest 組裝** | 抓取、計算、寫檔的實作 |
| `sources/twse.py` | M1／M2／O1–O3 抓取、節流、重試、回應分類、raw 快取 | 正規化、對帳、還原計算 |
| `sources/yahoo.py` | 依明確參數取得資料、記錄版本與參數、失敗隔離 | 決定是否採用其值 |
| `ticker_map.py` | `stock_id ↔ market ↔ twse_code ↔ yahoo_symbol` 與有效期間 | 任何行情邏輯 |
| `normalize.py` | raw → `canonical_quotes` 與 `corporate_actions`；型別／日期解析；還原係數 | 抓取、對帳、驗證分級 |
| `reconcile.py` | 跨來源比對、抽樣、產生差異報表 | **修改任何 canonical 值** |
| `market_data.py` | 契約定義、requested/loaded window、長↔寬轉換 | 抓取、因子 |
| `validation.py` | **資料契約層**的 FATAL／WARN 判定（`stage='contract'`） | **修復資料**（絕不自動修補）；**任何 rolling 或因子運算** |
| `universe.py` | 每日 mask 與 counts | 因子計算 |
| `factors.py` | 算子、FactorSpec、registry、12 因子；回傳 `FactorResult(values, diagnostics)`（`stage='factor'`） | I/O；決定診斷如何落檔 |
| `artifacts.py` | **純序列化與寫檔** | **流程控制、呼叫其他業務模組** |

**依賴規則**

1. `pipeline.py` 為星狀中心；其餘模組**不互相 import**（`ticker_map` 為唯一共用查表例外）。
2. `artifacts.py` 不得 import `factors`、`universe`、`market_data`、`normalize`、`sources`。
3. `core/research/` 內**不得** import Flask、Plotly、LINE SDK、`core/db_helper`、`core/strategies`、`core/calc_indicators`。

> **決策 X-4：為何需要 `pipeline.py`（相對修訂 1）。**
> 修訂 1 為線性鏈 `artifacts → factors → universe → market_data → validation → normalize → sources`，使 `artifacts.py` 成為事實上的流程控制者。問題有三：(a) 違反單一責任——寫檔模組不該決定順序；(b) 任何順序調整（例如把對帳移到驗證之後）要改動多個模組；(c) 單元測試必須 mock 整條鏈才能測一個階段。星狀協調後，順序只存在於一個檔案，各階段可獨立測試。
> **被拒替代**：把順序寫在 CLI → CLI 就變成業務模組，且無法被單元測試涵蓋。

---

## 5. Source Priority Matrix

### 5.0 市場範圍限定

**本 change 的研究 pipeline 僅涵蓋 TWSE 上市普通股。**

| 事實 | 後果 |
|---|---|
| M1 僅涵蓋上市 | 上櫃日行情不在本 pipeline |
| M2 僅涵蓋上市 | **上櫃無官方還原係數來源** |
| repo 既有 TPEx crawler 寫入 `daily_market_data`，但無 amount、無 corporate action、無 lineage | 不符本契約要求，不予採用 |

**不宣稱支援 TPEx。** `ticker_map` 的資料模型保留 `market` 與 `.TWO` 欄位作為未來能力，但：

- 本 change **不實作** TPEx adapter
- 本 change **不驗收**任何上櫃資料路徑
- `market='TPEx'` 的列在本 change 中**不進入** pipeline

### 5.1 逐欄位來源

| 資料 | 主來源 | yfinance | 覆蓋規則 |
|---|---|---|---|
| `raw_open/high/low/close` | TWSE RWD M1 | **僅對帳** | 主來源有值 → 永不覆蓋 |
| `volume`、`transaction_count` | 同上 | 僅對帳 | 同上 |
| `amount` | 同上（官方成交金額） | **不得覆蓋**（yfinance 無此欄） | 官方值缺 → `close × volume` proxy 且標記 |
| trading status（終止上市） | TWSE OpenAPI O1 | 不得覆蓋 | — |
| trading calendar | 「M1 判定為 `TRADING_DAY` 的日期」（實證） | 無 | OpenAPI O2 僅交叉檢查 |
| corporate actions | TWSE RWD M2 | 交叉驗證 | — |
| `event_factor` / `adjustment_factor` / `adjusted_*` | **本地計算** | 驗證 | `Adj Close` 永不寫入 |

### 5.2 預設模式為 `official_only`

| 模式 | 行為 | 預設 |
|---|---|---|
| `official_only` | yfinance **只做對帳**。主來源失敗即該日缺資料，記 WARN | ✅ **預設** |
| `allow_vendor_fallback` | 主來源失敗時允許以 yfinance 補列 | ❌ 需**明確 opt-in**（CLI 旗標 + manifest 記錄） |

**fallback 若啟用，必須全部滿足**：

1. **整列 OHLC 同來源**——四個價格來自 yfinance 同一次回應，不得與 TWSE 值混用
2. 該列 `is_fallback=true`、`fallback_reason` 非空、`raw_price_source='yfinance'`、`quality_status='degraded'`
3. `amount` **不得**由 fallback 提供（yfinance 無此欄）→ 該列 `amount` 為 NaN、`liquidity_basis='unavailable'`
4. `source_coverage.csv` 記錄主來源失敗與原因
5. manifest `warning_counts` 反映
6. manifest 記錄 `fallback_mode` 的實際值

### 5.3 絕對禁止

1. **靜默 fallback**
2. **自動平均兩個來源**
3. **混用不同來源組成同一筆 OHLC**
4. **yfinance 覆蓋 TWSE `amount`**
5. **調整價寫回 raw 欄位**
6. **對帳失敗後不留紀錄**
7. **主來源失敗被次來源靜默掩蓋**（三處記錄缺一即測試失敗）

---

## 6. Canonical Schema

★ 修訂 2：拆為**兩個**契約。行情表不再攜帶無法直接取得的欄位。

### 6.1 `canonical_quotes`（長表，主鍵 `(trade_date, stock_id)`）

| 欄位 | 型別 | 單位／格式 | 來源 | 必填 |
|---|---|---|---|---|
| `trade_date` | date | `YYYY-MM-DD`，Asia/Taipei naive | M1 | ✅ |
| `stock_id` | str | 正規化字串 | M1 | ✅ |
| `market` | str | 本 change 恆為 `TWSE` | ticker_map | ✅ |
| `currency` | str | 恆為 `TWD` | 常數 | ✅ |
| `raw_open` / `raw_high` / `raw_low` / `raw_close` | float | 元 | M1 | ✅ |
| `adjusted_open` / `adjusted_high` / `adjusted_low` / `adjusted_close` | float | 元 | 本地計算 | ❌（不可得為 NaN） |
| `adjustment_factor` | float | 累積係數，最新日為 1.0 | 本地計算 | ❌ |
| `adjustment_source` | str | `local_twse_twt49u` \| `unavailable` | lineage | ✅ |
| `adjustment_as_of` | datetime | 還原所依據的 corporate action 快照時刻 | lineage | ✅ |
| `volume` | int | **股** | M1 | ✅ |
| `amount` | float | **新台幣元** | M1 | ❌（缺則 proxy 或 NaN） |
| `transaction_count` | int | 筆 | M1 | ❌ |
| `liquidity_basis` | str | `official_amount` \| `close_times_volume_proxy` \| `unavailable` | 推導 | ✅ |
| `raw_price_source` | str | `twse_rwd` \| `yfinance` | lineage | ✅ |
| `is_fallback` | bool | — | lineage | ✅ |
| `fallback_reason` | str | 可為空 | lineage | ✅ |
| `quality_status` | str | `ok` \| `degraded` \| `unverified` | lineage | ✅ |
| `market_closed_at` | datetime | 該交易日的收盤時刻（見 §11） | 推導 | ✅ |
| `retrieved_at` | datetime | 向來源取得的時刻 | 執行期 | ✅ |
| `ingested_at` | datetime | 寫入 canonical 的時刻 | 執行期 | ✅ |
| `source_revision` | str | 來源回應識別（M1 的 `date` 欄位值） | 執行期 | ✅ |
| `quality_flags` | str | `;` 分隔的 WARN 代碼 | validation | ✅ |

**★ 已移除的欄位（修訂 2）**

| 移除欄位 | 移除理由 |
|---|---|
| `cash_dividend` | M2 **不提供分離的現金股利**，只有合併的「權值+息值」。保留該欄等同宣稱擁有實際上沒有的資料 |
| `stock_split_ratio` | 同上，M2 不提供分離的配股率 |
| `corporate_action_source` | 已移入 `corporate_actions` 契約 |
| `available_at` | 虛假精確（見 §11），改為 `market_closed_at` + `retrieved_at` |

### 6.2 `corporate_actions`（獨立契約，主鍵 `(ex_date, stock_id)`）

| 欄位 | 型別 | 說明 | 必填 |
|---|---|---|---|
| `ex_date` | date | 除權息基準日（M2「資料日期」，民國中文轉換而來） | ✅ |
| `stock_id` | str | 證券代號 | ✅ |
| `action_type` | str | `權` \| `息` \| `權息`（M2「權/息」原值） | ✅ |
| `pre_ex_close` | float | 除權息前收盤價（官方公告） | ✅ |
| `ex_reference_price` | float | 除權息參考價（官方公告） | ✅ |
| `event_factor` | float | `ex_reference_price / pre_ex_close` | ✅ |
| `source` | str | `twse_twt49u` | ✅ |
| `retrieved_at` | datetime | 取得時刻 | ✅ |

> **決策 X-5：為何拆成兩個契約。**
> 修訂 1 把 `cash_dividend`／`stock_split_ratio` 放進行情表，是**未經查證的假設**——實測後才知 M2 只給合併的「權值+息值」。除此之外，corporate action 的**自然主鍵是 `(ex_date, stock_id)`，不是 `(trade_date, stock_id)`**：一檔股票一年可能只有 1–2 個事件，卻有 250 個交易日。塞進行情表會產生 99% 的空值，且無法表達「同一天多個事件」。
> **被拒替代**：保留欄位但恆為 NaN → 契約宣稱擁有實際上取不到的資料，違反憲法第十二條的精神（不得宣稱不存在的條件）。

### 6.3 設計約束

- **原始價與調整價分離。** `raw_*` 一旦寫入即不可變；`adjusted_*` 為衍生值。
- **`close × volume` 僅為近似**，必須由 `liquidity_basis` 明示。
- **Point-in-Time vs as-of**：`raw_*` 為 point-in-time 事實；`adjusted_*` 為 **as-of 快照**，依賴 `adjustment_as_of` 時點已知的事件。因子若使用 `adjusted_*`，**不同 `adjustment_as_of` 的 run 不可直接比較**。

---

## 7. Raw / Adjusted Price

### 7.1 還原係數計算

對每個 `stock_id`，取 `corporate_actions` 中該股全部事件，依 `ex_date` 排序：

```
event_factor(e) = ex_reference_price(e) / pre_ex_close(e)          # 皆為官方公告值
adjustment_factor(t) = Π { event_factor(e) : ex_date(e) > t }      # 由最新日往回累乘
adjusted_close(t)    = raw_close(t) × adjustment_factor(t)
```

`adjusted_open/high/low` 使用同一係數。最新日的 `adjustment_factor` 為 1.0。

### 7.2 事件涵蓋範圍

| 事件 | 涵蓋 | 說明 |
|---|---|---|
| 現金股利 | ✅ | `action_type='息'`；官方參考價已內含 |
| 股票股利／拆併股 | ✅ | `action_type='權'` 或 `'權息'` |
| 減資 | ⚠️ **不涵蓋** | 不走 TWT49U。**已知限制**，必須寫入 `docs/` 與 manifest |
| 事件修訂 | ⚠️ | TWSE 事後修訂會使重跑得到不同係數 → 由 `adjustment_as_of` 表達 |

### 7.3 ★ 因子的價格基準（修訂 2 變更）

| 用途 | `price_basis` | 輸出位置 | 正式結果 |
|---|---|---|---|
| **正式價格型因子** | `local_adjusted` | `values/<factor>/<year>.csv` | ✅ |
| QA 對照 | `raw_unadjusted` | `qa/values_raw/<factor>/<year>.csv` | ❌ |
| 非價格型因子（`volume_ratio_20d`） | `not_applicable` | `values/...` | ✅ |

**規則**

1. 價格型因子正式輸出使用**還原價**。
2. **無可靠還原價的股票，正式因子值為 NaN，不得以 raw 冒充。** 判定：`adjustment_source == 'unavailable'` 或 `adjustment_factor` 為 NaN。
3. raw 版本輸出至**獨立目錄與獨立檔名**，避免下游誤用。
4. `factor_version` 於本次改動遞增。
5. `price_basis` 寫入每一列與 manifest。

> **決策 X-1（修訂）：正式輸出改用 `local_adjusted`。**
> 修訂 1 主張先用 raw，理由是「若 Rank IC 異常，無法區分還原錯或因子錯」。該顧慮成立，但解法不必是放棄還原——**同時產出 raw QA 對照**即可完整歸因：兩組因子的差異必然來自還原層。
> 使用原始價的代價是**已知且系統性的錯誤**（除息日假跌幅），不是可揭露的殘留誤差。以官方公告參考價還原，其誤差遠小於不還原。

### 7.4 vendor-adjusted vs locally-adjusted

| | locally-adjusted（本設計） | yfinance `Adj Close` |
|---|---|---|
| 計算依據 | TWSE 官方公告參考價 | Yahoo 內部規則，未公開 |
| 可重現 | ✅ | ❌ |
| 可稽核 | ✅（每個係數對應一筆官方事件） | ❌ |
| 會被回溯重寫 | 由 `adjustment_as_of` 明示 | **會，且無通知** |

> `Adj Close` **不得**被視為不可變的歷史事實。對帳差異超標為 **WARN**，非 FATAL——差異成因很可能在 vendor 端。

---

## 8. ★ 算子語義（修訂 2 新增／修正）

### 8.1 「未定義」與「合法為零」必須區分

修訂 1 的規則「常數序列 → NaN」**過度套用**。統計上，常數序列的標準差**就是 0**，那是一個有意義的值（零波動），不是未定義。

| 情境 | 正確結果 | 理由 |
|---|---|---|
| 窗口未滿（觀測數 < `min_periods`） | **NaN** | 資訊不足，未定義 |
| 窗口已滿，序列為常數 | **`ts_std` 回 `0`** | 零離散度是合法的量測結果 |
| 相關係數的任一邊標準差為 0 | **NaN** | 相關係數的分母為零，數學上未定義 |
| z-score 的分母標準差為 0 | **NaN** | 同上 |
| 任何除法的分母為 0 | **NaN** | 未定義；產生的 `Inf` 一律轉 NaN |

**後果與揭露**：`realized_vol_20d` 對真正不動的股票會得到 `0`，落在最低波動分位——這是**正確**的。但停牌、跌停鎖死也會產生同樣的 0。因此 `W002_zero_volume` 與 `W012_zero_dispersion` 必須記錄，供後續研究判斷。**不以扭曲數學定義的方式處理，而以揭露處理。**

> **`W012` 由 `factors.py` 產生，不由 `validation.py` 產生**（決策 X-6）。零離散度要等 rolling 算完才知道；由 validation 產生會迫使它偷做因子運算。

### 8.2 缺值政策

| 情況 | 處理 |
|---|---|
| rolling `min_periods` | **等於完整窗長** |
| `pct_change` | **`fill_method=None`**，缺值不得被跨越 |
| 除以 0 產生的 `Inf` | **一律轉 NaN** |
| NaN 傳播 | **一路傳到輸出** |
| 輸出中的 Inf | **禁止**，測試強制斷言 |
| 非正成交量 | 統一以 **`volume.where(volume > 0)`** 轉 NaN，**在任何除法或對數之前** |

### 8.3 Wilder ATR 的明確定義

```
TR(1) = high(1) − low(1)                                  # ★ 首筆無前收盤，僅用當日高低
TR(t) = max( high(t) − low(t),
             |high(t) − close(t−1)|,
             |low(t)  − close(t−1)| )        for t ≥ 2

seed  = mean( TR(1) … TR(n) )                             # ★ n = 14，前 n 筆 TR 的簡單平均
ATR(n)     = seed
ATR(t)     = ( ATR(t−1) × (n−1) + TR(t) ) / n             for t > n
```

**lookback 推導（修訂 3 更正）**

| bar | 可得的 TR | 需要的 bar |
|---|---|---|
| 1 | `TR(1) = high(1) − low(1)` | 僅 bar 1（無前收盤） |
| 2 | `TR(2)` | bar 1–2 |
| … | … | … |
| 14 | `TR(14)` | bar 13–14 |

14 根日線即湊滿 14 筆 TR → `seed` 在第 **14** 根成立 → `ATR` 的第一個非 NaN 值在第 **14** 根。

- `natr_14d = ATR(14) / close`，**不乘 100**
- **`natr_14d` 的 `lookback` = 14**

> **修訂 3 更正**：修訂 2 誤植 `lookback = 15`，理由寫「TR(2) 起需要前收盤，故需 15 筆日線」。該理由錯誤——TR(2) 所需的前收盤是 **bar 1**，已包含在前 14 根之內，不需額外一根。修訂 2 同時聲稱「第一個非 NaN 在第 14 筆」卻標 `lookback=15`，**自相矛盾，first-valid-date 測試必然失敗**。

### 8.4 ★ `price_volume_corr_20d` 的 lookback 修正

```
ret       = close.pct_change(fill_method=None)      # 需 1 筆前值
log_dvol  = log(volume.where(volume > 0)).diff()    # 需 1 筆前值
factor    = ret.rolling(20).corr(log_dvol)          # 需 20 筆 ret 與 20 筆 log_dvol
```

需要 20 筆 `ret` → 需要 21 筆 `close`。**`lookback = 21`**（修訂 1 誤植為 22）。

---

## 9. Reconciliation

### 9.1 比對範圍與門檻

逐 `(trade_date, stock_id)` 比對 TWSE 主來源與 yfinance：

| 欄位 | 方式 | 門檻 |
|---|---|---|
| `raw_close` | 相對誤差 | > 0.5% → `W004_recon_mismatch` |
| `raw_open/high/low` | 相對誤差 | > 0.5% → `W004` |
| `volume` | 相對誤差 | > 1% → `W004`（yfinance 台股量單位有已知差異） |
| `adjusted_close` vs `Adj Close` | 相對誤差 | > 1% → `W004` |
| corporate action 日期 | 集合差集 | 任一方獨有 → `W005_action_inconsistent` |

### 9.2 ★ 抽樣策略（修訂 2 修正）

全市場逐日對帳需 1,000+ 次 yfinance 請求，不可行。採**確定性分層抽樣**：

| 層 | 選取方式 |
|---|---|
| 固定層 | **區間內平均成交金額（`amount`）前 20 檔** |
| 隨機層 | 由固定的 `reconciliation_seed` 決定的 30 檔 |
| 事件層 | 全部有 corporate action 的股票日 |

**修正說明**

1. **不使用「市值前 20」。** 修訂 1 寫「市值前 20（以 amount 排序代理）」是自相矛盾的措辭——本 pipeline **沒有市值資料**（`t187ap03_L` 有已發行股數，但未納入本 change）。改為明確的**成交金額前 20**。
2. **`reconciliation_seed` 為獨立的固定整數**，寫入 manifest。**不得使用 `run_id` 作為種子**——`run_id` 含 UTC 時間戳，每次執行都不同，會使「確定性抽樣」名存實亡。
3. 抽樣比例與實際覆蓋數寫入 `reconciliation_summary.csv` 與 manifest。
4. **未被抽樣的列 `quality_status='unverified'`，不得標為 `ok`。**

> **決策 X-3（修訂）：抽樣而非全量。**
> 代價：對帳只能給出「抽樣未發現系統性偏差」，不是「全部正確」。此限制必須在 `docs/` 明載。

### 9.3 對帳不得修改資料

比對結果**只**產生診斷與報表。以測試斷言：對帳前後的 canonical DataFrame 完全相等。

---

## 10. FATAL／WARN 矩陣

**界線**：「會讓後續運算**靜默算錯** → FATAL；只是資料**長得奇怪** → WARN」。
**分級寫死於程式，不得由執行參數降級**（訪談 D-15）。

### 10.1 FATAL（中止，且**不得留下成功狀態 manifest**）

| 代碼 | 條件 |
|---|---|
| `F001_missing_column` | canonical 必填欄缺失 |
| `F002_duplicate_key` | `(trade_date, stock_id)` 或 `(ex_date, stock_id)` 重複 |
| `F003_unparseable_date` | 日期無法解析 |
| `F004_nonpositive_price` | 任一 `raw_*` ≤ 0 |
| `F005_high_lt_low` | `raw_high < raw_low` |
| `F006_ohlc_out_of_range` | `raw_open` 或 `raw_close` 不在 `[raw_low, raw_high]` |
| `F007_negative_volume` | `volume < 0` |
| `F008_wide_misalignment` | 長轉寬後 index／columns 不符 |
| `F009_schema_drift` | 來源回應缺少已宣告的來源欄位；或收盤行情 table 匹配數不為 1（0 個或多個） |
| `F010_negative_amount` | `amount < 0` |
| **`F011_window_before_source_start`** | 請求日期早於來源歷史下界（實測 2004-02-11）。暖機區間不完整卻看似正常 → 長 lookback 因子產出「有值但錯」 |

### 10.2 WARN（記錄後繼續，必須輸出 artifact）

★ 修訂 3：新增 `stage` 欄，標示該診斷由哪個階段產生。

| 代碼 | `stage` | 產生模組 | 條件 |
|---|---|---|---|
| `W001_suspicious_gap` | `contract` | `validation.py` | `\|raw_close / prev_raw_close − 1\| > 20%` |
| `W002_zero_volume` | `contract` | `validation.py` | `volume == 0` |
| `W003_universe_count_jump` | `contract` | `validation.py` | 單日檔數相對前一交易日變動 > ±30% |
| `W004_recon_mismatch` | `contract` | `reconcile.py` | §9.1 任一門檻超標 |
| `W005_action_inconsistent` | `contract` | `reconcile.py` | corporate action 兩來源不一致 |
| `W006_liquidity_proxy` | `contract` | `normalize.py` | 使用 `close_times_volume_proxy` |
| `W007_adjustment_unavailable` | `contract` | `normalize.py` | 無法計算還原價 |
| `W008_fallback_used` | `contract` | `normalize.py` | 該列來自次來源 |
| `W009_unmapped_ticker` | `contract` | `ticker_map.py` 呼叫端 | 查無有效對應 |
| `W010_source_empty` | `contract` | `sources/twse.py` | 來源回應 `OK` 但無資料 |
| `W011_source_error` | `contract` | `sources/twse.py` | 來源錯誤／重試耗盡／回應狀態不明 |
| **`W012_zero_dispersion`** | **`factor`** | **`factors.py`** | 窗口已滿但序列為常數（`ts_std` 回 0，見 §8.1） |
| **`W013_request_out_of_range`** | `contract` | `sources/twse.py` | 請求日期晚於今日（`OUT_OF_RANGE` `bound='future'`）。跳過該日，不重試 |

> **`validation.py` 只產生 `stage='contract'` 且不涉及 rolling 的規則。** `W012` 為 `stage='factor'`，由 `factors.py` 於 `FactorResult.diagnostics` 中回傳，`pipeline.py` 合併後統一落檔（決策 X-6）。

### 10.3 Inf 禁止

`Inf`／`-Inf` 在 canonical 資料與因子輸出中**一律禁止**，須在產生它的算子內轉為 NaN。

---

## 11. ★ Point-in-Time 語義（修訂 2 修正）

| 名詞 | 定義 | 實作 |
|---|---|---|
| `trade_date` | OHLCV 所屬交易日（T） | ✅ |
| **`market_closed_at`** | 該交易日的**收盤時刻**（來自交易所規則，非資料發布時刻） | ✅ |
| **`retrieved_at`** | 本系統實際向來源取得該筆資料的時刻 | ✅ |
| `signal_time` | T 日收盤後；因子只能使用 `trade_date <= T` 的資料 | ✅（look-ahead sentinel 強制） |
| `execution_time` | **T+1**——T 日資料只能於 T+1 執行 | ❌ 為 forward return 階段預留 |
| `adjustment_as_of` | 還原係數所依據的事件快照時刻 | ✅ |
| 時區 | Asia/Taipei naive，**不做 tz 轉換** | ✅ |

**★ 移除 `available_at = 14:30` 的理由**

修訂 1 宣稱資料於 T 日 14:30 可得。這是**虛假精確**：

1. TWSE 的收盤時刻與**資料發布時刻**是兩件事，後者會變動且未經查證（`_baseline` V5 待驗證）。
2. 一個未經驗證的精確時刻，會讓下游誤以為可以做日內時序推論。

**改採的防前視機制不依賴時刻**：

> **T 日的資料只能於 T+1 執行。** 因子在 T 日收盤後計算，執行時點為 T+1。
> 只要遵守此規則，即使 T 日資料實際於 15:30 才發布，也不會產生前視。

這比「宣稱 14:30 可得」更保守，且**不需要知道確切發布時刻**即可成立。

**與既有回測刻意分歧**：`core/backtest/runner.py:983-988` 以同日收盤價成交。本 change **不修改它**（受 `test/characterization/test_trade_sequence_regression.py` 保護）。差異為設計選擇，非 bug。

---

## 12. Failure Matrix

| 情境 | pipeline 行為 | manifest | artifact |
|---|---|---|---|
| M1 判定 `TRADING_DAY` | 正常處理 | — | — |
| M1 判定 `NON_TRADING_DAY` | 跳過該日，**不計為警告** | — | `source_coverage.csv` 標為非交易日 |
| M1 判定 `OUT_OF_RANGE` `bound='future'` | 跳過該日，**不重試** | `warning_counts` +1 | `W013` + `source_coverage.csv` |
| M1 判定 `OUT_OF_RANGE` `bound='early'` | **FATAL** `F011`，**不重試** | **不寫成功 manifest** | 寫 `validation_report.csv` 後中止 |
| M1 判定 `EMPTY_RESULT` | 跳過該日 | `warning_counts` +1 | `W010` + `source_coverage.csv` |
| M1 判定 `SOURCE_ERROR` | 重試；耗盡後 `official_only` → 該日缺資料；opt-in fallback → 用 yfinance 並標記 | `warning_counts` +1 | `W011` + `source_coverage.csv`（+ `is_fallback` 若適用） |
| M1 缺宣告欄位，或收盤行情 table 匹配數不為 1 | **FATAL** `F009` | **不寫成功 manifest** | 寫 `validation_report.csv` 後中止 |
| M2 全區間無資料 | 全部 `adjustment_source='unavailable'`；**價格型正式因子為 NaN** | `warning_counts` +N | `W007` |
| yfinance 全部失敗 | **不影響 canonical 與因子輸出** | `reconciliation_coverage=0` | `reconciliation_summary.csv` 僅表頭 |
| yfinance `repair_status` 非空 | 該股對帳標 `unverified` | — | `reconciliation_summary.csv` |
| ticker 查無對應 | 該股不對帳 | `warning_counts` +1 | `W009` |
| 驗證 FATAL | **立即中止** | **manifest 不存在或 `status='failed'`** | `validation_report.csv` 仍須寫出 |
| 某因子 `required_columns` 不齊 | 該因子跳過，其他因子繼續 | 該因子標 `skipped` | manifest |

> **不變式**：`status='success'` **當且僅當**零個 FATAL。

---

## 13. Artifact Layout

```
artifacts/factors/<run_id>/
├── run_manifest.json
├── validation_report.csv          # 欄位：stage, code, severity, trade_date, stock_id, detail
├── source_coverage.csv
├── reconciliation_summary.csv
├── universe_counts.csv
├── corporate_actions.csv          # ★ 獨立契約的落檔
├── _raw/
│   ├── twse_rwd/MI_INDEX_<YYYYMMDD>.json
│   ├── twse_rwd/TWT49U_<start>_<end>.json
│   ├── twse_openapi/<endpoint>.json
│   └── yfinance/<yahoo_symbol>.csv
├── values/                        # ★ 正式：local_adjusted
│   └── <factor_name>/<year>.csv
└── qa/                            # ★ QA 對照：raw_unadjusted
    └── values_raw/<factor_name>/<year>.csv
```

長表欄位：`trade_date, stock_id, factor_name, factor_version, value, price_basis, run_id`

`<run_id>` 格式：`<UTC timestamp>_<git short sha>`，由 CLI 產生並傳入（計算模組內**不得**呼叫 `datetime.now()`）。

### 13.1 `run_manifest.json` 最低內容

```json
{
  "run_id": "...",
  "status": "success | failed",
  "generated_at": "ISO8601",
  "git_commit": "...",
  "market_scope": "TWSE",
  "fallback_mode": "official_only | allow_vendor_fallback",
  "reconciliation_seed": 0,
  "source_versions": {
    "twse_rwd": {"endpoints": ["MI_INDEX", "TWT49U"], "fetched_at": "..."},
    "twse_openapi": {"swagger_paths": 143, "fetched_at": "..."},
    "yfinance": {"package_version": "0.2.66"}
  },
  "source_parameters": {
    "yfinance": {"auto_adjust": false, "actions": true, "keepna": true,
                 "repair": false, "interval": "1d"}
  },
  "factor_versions": {"momentum_20d": "2.0.0", "...": "..."},
  "requested_window": {"start": "...", "end": "..."},
  "loaded_window":    {"start": "...", "end": "..."},
  "maximum_lookback": 253,
  "adjustment_as_of": "ISO8601",
  "universe_rule": {
    "code_regex": "^[1-9]\\d{3}$",
    "market": "TWSE",
    "price_column": "raw_close",
    "min_raw_close": 10,
    "min_liquidity_twd": 20000000,
    "liquidity_window": 20,
    "liquidity_proxy_formula": "raw_close * volume",
    "require_positive_volume": true
  },
  "price_basis": {"primary": "local_adjusted", "qa": "raw_unadjusted"},
  "row_counts": {"canonical_quotes": 0, "corporate_actions": 0, "factor_values": 0},
  "warning_counts": {"W001_suspicious_gap": 0, "...": 0},
  "universe_count_median": 0,
  "reconciliation_coverage": {"sampled_symbols": 0, "total_symbols": 0}
}
```

---

## 14. Memory Strategy

12 因子 × 約 1,000 檔 × 3 年（約 750 交易日）。單張寬表 750 × 1,000 float64 ≈ 6 MB。5 張價量寬表 ≈ 30 MB。峰值可控。

因子輸出為 12 × 750 × 1,000 = 900 萬列（正式）＋ 同量（QA raw），故：

1. **不輸出單一巨型 CSV**，逐因子逐年度分區。
2. **逐因子計算後立即寫檔並釋放**，不同時保留 12 張因子寬表。
3. 正式與 QA 兩版**逐因子成對計算後即寫出**，不累積。
4. raw response 快取落地，重跑不重抓。

---

## 15. 載入視窗與 Universe Mask 順序

### 15.1 requested vs loaded window

```
loaded_start = requested_start − (maximum_lookback + 10 個交易日安全邊際)
loaded_end   = requested_end
```

`maximum_lookback` = registry 中最大者（目前 `momentum_12_1` = 253）。輸出僅含 requested 區間。

### 15.2 強制順序

```
完整行情（含暖機）→ 計算因子 → 建立 universe mask → 套用 mask 輸出
```

**禁止**先依 universe 裁切行情再算 rolling 因子。某股在 T 日不符門檻，其 T−20 至 T 的價格序列仍是計算 T+5 因子的必要輸入；先裁切會在時序上打洞，使 rolling 窗口跨越缺口而**靜默算錯**。

### 15.3 ★ Universe 使用原始價，因子使用還原價（修訂 3 新增）

| 用途 | 使用的價格 |
|---|---|
| **價格門檻** | **`raw_close >= 10`** |
| **流動性（官方）** | `amount`（官方成交金額，本身即為當日實際成交的新台幣金額，屬 raw 語義） |
| **流動性（近似）** | **`raw_close × volume`** |
| **正式因子** | `adjusted_*`（`local_adjusted`） |
| QA 對照因子 | `raw_*` |

> **決策 X-7：universe 門檻必須用 raw，不能用 adjusted。**
>
> `adjusted_close` 是依 `adjustment_as_of` 變動的**回溯快照**。若以它建立歷史股票池，會產生一個嚴重後果：
>
> **同一個歷史交易日的 universe 成員，會因為「未來」新增的公司行動而改變。**
>
> 例：某股 2023-06-01 的 `raw_close = 12`（可交易、過門檻）。若它在 2024 年配息使累積係數變成 0.8，則 `adjusted_close(2023-06-01) = 9.6` → 在新的 run 中它會**退出** 2023-06-01 的股票池。同一天、同一檔股票，只因為一年後發生的事而改變歷史成員資格。這使 universe 不可重現，也不符「當日實際可交易」的語義。
>
> **價格門檻的意義是「那一天實際的股價是否高於跳動單位失真區」**，那是 point-in-time 事實，只能用 raw。
>
> **這個 raw／adjusted 的不對稱是刻意的**，不是疏漏：
> - **可交易性判斷** → 當時的實際價格（raw）
> - **報酬率計算** → 連續的價格序列（adjusted）
>
> 兩者問的是不同的問題，故用不同的價格。此不對稱必須寫入限制文件，避免被後人「修正」成一致。
>
> **被拒替代**：universe 也用 adjusted → universe 隨 `adjustment_as_of` 漂移，歷史不可重現。

---

## 16. Rollout / Rollback

### 16.1 Rollout

| 階段 | 內容 |
|---|---|
| Shadow | 獨立執行，只寫 `artifacts/`，**不寫 DB、不改既有流程、不註冊排程** |
| 評估 | 人工檢視 validation／reconciliation／universe counts／raw-vs-adjusted 差異 |
| 後續 change | TPEx 支援；修正正式 ingestion 保留 amount；建 `factor_values` 表 |

### 16.2 ★ Rollback（修訂 2 修正）

| 層級 | 方式 |
|---|---|
| **整個 change** | **依 Phase commits 逆序 `git revert`**；若以 merge commit 併入，則 `git revert -m 1 <merge commit>` |
| 單一 Phase | 該 Phase 的 commits 逆序 revert |
| 單一 task | 該 task 的 commit revert（僅在後續 task 未依賴它時有效） |
| 資料 | 刪除 `artifacts/factors/<run_id>/` |
| 外部副作用 | **無** |

> 修訂 1 宣稱「`git revert` 單一 commit 即可完全回復」與 tasks.md 的「一任務一 commit」自相矛盾。修訂 2 改為依 Phase／merge commit 回復。

---

## 17. 被拒絕方案

| # | 方案 | 拒絕理由 |
|---|---|---|
| A1 | 以 TWSE OpenAPI 為歷史主來源 | 143 個 endpoint **全部無參數**；技術上不可能 |
| A2 | 以 `STOCK_DAY?stockNo=` 逐股逐月回補 | 請求數約為 `MI_INDEX` 的 50 倍 |
| A3 | 以 yfinance 為歷史主來源 | 台股品質不穩、無 `amount`、`Adj Close` 不可稽核 |
| A4 | 直接採用 `Adj Close` 作為 `adjusted_close` | vendor-adjusted 不可重現、不可稽核 |
| A5 | 由現金股利與配股率自行推導還原係數 | **M2 根本不提供分離的股利與配股率**；且等同重新實作交易所公式 |
| **A6** | ~~本 change 以 raw 計算正式因子~~ | **修訂 2 推翻**。原顧慮（歸因困難）以同時產出 raw QA 對照解決；不還原是**已知且系統性的錯誤** |
| A7 | 修正 `jobs/update_database.py` 保留 amount | 改動正式 ingestion 與 schema，屬不同性質工作。已記於 TD-02a |
| A8 | 重用 `core/calc_indicators.py` 既有指標 | `:190, 324, 429` 一律 `fillna(0)`；線上選股與研究的需求相反 |
| A9 | 全量逐股對帳 | yfinance 無批次 endpoint；改用確定性分層抽樣 |
| A10 | FATAL／WARN 可由參數降級 | 第一次卡住即會被降級且永不調回 |
| A11 | 因子數值 golden 快照測試 | 只告知「數字變了」，不告知「變得對不對」，違反憲法第十五條 |
| A12 | 拆成兩個 change | 契約必須由實際來源能力決定（`cash_dividend` 即為實例） |
| A13 | 本 change 建立 `factor_values` DB 表 | 零個已驗證因子時做 schema 決策過早 |
| A14 | D2 實作 `rank_cs` / `rank_ts` | 12 個因子無一使用 |
| A15 | 研究模組放入 `core/validation/` | 該目錄為策略穩定性驗證，僅名稱相撞 |
| **A16** | 由 `artifacts.py` 承擔流程控制（修訂 1 設計） | **修訂 2 推翻**。寫檔模組不該決定順序；見決策 X-4 |
| **A17** | 本 change 納入 TPEx | M1／M2 皆僅涵蓋上市；**上櫃無官方還原係數來源**，強行納入等同以 raw 冒充 adjusted |
| **A18** | `cash_dividend` / `stock_split_ratio` 留在行情契約 | M2 只提供合併的「權值+息值」；保留欄位等同宣稱擁有取不到的資料 |
| **A19** | 常數序列的 `ts_std` 回 NaN | **修訂 2 推翻**。零離散度是合法量測結果；NaN 只保留給窗口不足與分母為零 |
| **A20** | 以 `run_id` 作為對帳抽樣種子 | `run_id` 含時間戳，每次不同，「確定性抽樣」名存實亡 |
| **A21** | ticker mapping 的 runtime 資料放在 `test/fixtures/` | 正式執行相依於測試目錄；fixtures 應僅供測試 |

---

## 18. ★ 未決問題（修訂 2 重新分類）

修訂 1 把 U1／U3／U4 同時列為「apply 的前置條件」與「Phase 0 的任務」，形成循環：未完成不得 apply，但不 apply 就無法執行 Phase 0。修訂 2 明確區分兩類。

### 18.1 Pre-apply investigation（**apply 前必須關閉**，不需寫任何程式碼）

| # | 問題 | 關閉方式 |
|---|---|---|
| ~~**P1**~~ | 本 change 要回補的實際歷史區間 | ✅ **已完成 2026-07-30**：`2023-01-03` → `2026-07-28`。見 `_baseline/backfill_scope.md` |
| ~~**P2**~~ | B2 baseline 的凍結環境條件 | ✅ **已完成 2026-07-30**：凍結於 **MySQL NOT REACHABLE**、HEAD `a6daf10`；`372 passed / 2 failed / 3 errors`，兩輪一致。**5 項失敗全部同源於 `core/db_helper.py:819` ConnectionError**，與程式碼缺陷無關。見 `_baseline/test_baseline.md` |
| ~~**P3**~~ | `MI_INDEX` 在非交易日／未來日／過早日期的回應差異 | ✅ **已完成 2026-07-30**，見 `_baseline/source_capability.md` §2.2 |
| ~~**P4**~~ | `MI_INDEX` 整份 response 的 `tables` 總數與各 table 用途 | ✅ **已完成 2026-07-30**，見 `_baseline/source_capability.md` §2.2b |

> **P1–P4 皆為唯讀調查，可在 apply 前完成。** 其產出寫入 `_baseline/`。
> **✅ 現況：P1–P4 全部關閉，apply 的前置條件已滿足。**

### 18.2 Implementation-time investigation（實作期間關閉，**不阻擋 apply**）

| # | 問題 | 對應 Phase | 若未達預期 |
|---|---|---|---|
| I1 | `daily_market_data` 歷史深度與是否保留下市股票歷史列 | **Phase 12.4** | 僅影響存活者偏差的**揭露措辭**，不影響 pipeline 正確性 |
| I2 | 套用門檻後實際每日檔數 | **Phase 11.2** | **僅記錄，不阻擋**。中位數 < 400 時寫入 manifest 與限制文件，供後續 D3 判斷分位數穩定性 |
| I3 | TWSE RWD 實際 rate limit | Phase 2.3 | 以保守節流起步，實測後調整 |
| I4 | TWSE 每日行情實際發布時刻 | **不排入 Phase** | 已由「T 日資料 T+1 執行」規避（§11）；無須查證即可保證無前視 |
| I5 | 上櫃是否有可用的官方除權息來源 | 不在本 change | 後續 TPEx change |
| I6 | 減資事件的官方來源 | 不在本 change | 列為已知限制 |
| I7 | 歷史深度不足時 `momentum_12_1` 的處置 | Phase 11 | 該因子全為 NaN，由 first-valid-date 測試揭露；不移除 |

> **★ I2 不再是阻擋條件。** 修訂 1 將「< 400 檔即停止」列為 Phase 11.2 的阻擋條件。但檔數是**研究品質訊號**，不是**正確性缺陷**——pipeline 在 300 檔下依然正確運作。強行阻擋會讓 D1／D2 因為一個屬於 D3 的顧慮而無法交付。改為記錄於 manifest（`universe_count_median`）與限制文件。

---

## 19. 進入 apply 的條件

1. proposal、design、tasks 與 **6 份 spec** 經使用者核准
   （`research-pipeline-orchestration`、`research-market-data-sources`、`research-market-data-contract`、`research-data-validation`、`research-universe`、`research-factor-engine`）
2. `openspec validate 2026-07-30-factor-research-mvp --strict` 通過
3. **§18.1 的 P1–P4 全部關閉**（唯讀調查，產出於 `_baseline/`）

---

## 20. 訪談決策的變更記錄

| 訪談決策 | 狀態 | 說明 |
|---|---|---|
| D-01 CLI 入口 | ✅ 維持 | CLI 僅解析參數，改呼叫 `pipeline.run()` |
| D-02 只寫 artifacts、不碰 DB | ✅ 維持 | |
| **D-03 原始未還原價** | ❌ **被推翻兩次** | 修訂 1：納入還原價但因子仍用 raw。修訂 2：**正式因子改用 `local_adjusted`**，raw 降為 QA 對照 |
| D-04 T+1 時間語義 | ✅ 維持並強化 | 移除 `available_at=14:30` 的虛假精確，改以「T 日資料 T+1 執行」規避前視 |
| D-05 僅普通股 `^[1-9]\d{3}$` | ✅ 維持 | |
| D-06 每日動態股票池 | ✅ 維持 | |
| **D-07 流動性以 `close × volume` 近似** | ⚠️ 升級 | 優先官方 `amount`；proxy 為 fallback 且須標記 |
| D-08 內部寬表、輸出長表 | ✅ 維持 | |
| **D-09 因子清單** | ⚠️ 微調 | `reversal_5d` → `return_5d`；`amihud_20d` 改用官方 amount |
| **D-10 自寫算子、嚴格 NaN** | ⚠️ **修正** | 「常數序列 → NaN」為過度套用。`ts_std` 合法回 0；NaN 保留給窗口不足與分母為零（§8.1） |
| **D-11 FactorSpec 7 欄** | ⚠️ 擴充 | 增為 10 欄（+`version`、`family`、`unit`）。`direction` 仍全為 0 |
| **D-12 12 因子公式** | ⚠️ 修正 | `price_volume_corr_20d` lookback 22→21；NATR 首筆 TR 與 seed 明確化；量能類統一 `volume.where(volume>0)` |
| D-13 `rank_cs`/`rank_ts` 延後 | ✅ 維持 | |
| **D-14 阻擋條件 4 條** | ⚠️ 擴充 | 增為 15 條 |
| D-15 FATAL／WARN 寫死 | ✅ 維持並擴充 | 新增 `W010`–`W012` |
| **D-16 change 目錄日期** | ⚠️ 更新 | `2026-07-29` → `2026-07-30` |
| **D-17 4 個 capability** | ⚠️ 擴充 | 增為 5 個。不建 ADR 維持 |
| **D-18 `core/research/` 4 檔** | ⚠️ 擴充 | 增為 `pipeline.py` + `sources/` + `resources/` + `normalize` + `reconcile` + `artifacts`。憲法第十條的「不拆」項目維持（算子不獨立成檔、`FactorSpec` 不獨立成檔） |
