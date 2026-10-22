# 每日選股資料缺口與資料閘門

- 日期：2026-10-22
- 證據：`outputs/daily_selection_recovery/2026-10-22/data_quality_report.json`、`official_chip_manifest.json`、`model_contract_report.json`
- 原則：缺值保留 NULL 並排除；官方資料缺漏時不補造；未經官方對照的來源不得判 DATA_PASS。

## 1. 逐來源資料閘門

| 閘門 | 狀態 | 範圍 | 證據與缺口 |
|---|---|---|---|
| PRICES_TWSE | DATA_PASS | 2023-01-03～2026-07-28 | 沿用 native7 的官方 MI_INDEX 快照（MySQL OHLC 吻合 99.70%），本輪未重建 |
| CALENDAR | DATA_PASS | 同上 | 2026-07-28 之後未認證；2026-09-28～10-21 只依 T86／MI_MARGN 回應判定是否開市（2 個平日休市） |
| TAIEX_BENCHMARK | DATA_PASS | 同上 | 只作 Benchmark。策略內的大盤過濾讀 `MARKET_SYMBOL=2330`（屬策略規則），兩者分開 |
| UNIVERSE_TWSE | DATA_PASS | 同上 | 官方每日清單 |
| UNIVERSE_TPEX | DATA_BLOCKED | — | 沒有官方上櫃行情快取；研究端排除上櫃。recovered 每日選股仍會讀到 MySQL 的上櫃列，屬未認證範圍（OD-8） |
| INSTITUTIONAL_FLOWS_OFFICIAL（T86） | DATA_PASS | 2025-12-15～2026-07-28、2026-09-28～10-21 | 165 個請求日，其中 163 個開市日回應 OK，每檔 SHA-256 已記錄。有成交但不在 T86 的股票日共 1,716 筆，保留 NULL，不視為 0 |
| MARGIN_OFFICIAL（MI_MARGN） | DATA_PASS | 同上 | 有成交但沒有融資資料的股票日 5,007 筆（非信用交易標的等），保留 NULL |
| MySQL 外資（研究期） | DATA_FAIL | 共同鍵 148,818 | 19,846 筆不符；19 個整日 MySQL 存成 0，官方為非零（例如 2025-12-31、2026-01-02、2026-04-15、2026-05-19、2026-05-20、2026-06-11）；另有 7,208 筆 MySQL 為 NULL 但官方有值 |
| MySQL 投信（研究期） | DATA_FAIL | 共同鍵 148,818 | 6,050 筆不符（同樣的整日 0 問題，另有零星日期） |
| MySQL 自營商／融資（研究期） | DATA_BLOCKED | — | 2026-10-12 以前全為 NULL（自營商 156,026 筆、融資 152,971 筆官方有值） |
| MySQL 外資（2026-10） | DATA_PASS | 共同鍵 15,062 | 0 筆不符 |
| MySQL 投信（2026-10） | DATA_FAIL | 共同鍵 15,062 | 53 筆不符（2026-10-07、10-16、10-20） |
| MySQL 自營商（2026-10） | DATA_PASS | 共同鍵 6,456 | 0 筆不符；但只有 10-12～10-16、10-20 有值，10-19 為 NULL |
| MySQL 融資（2026-10） | DATA_PASS | 共同鍵 6,322 | 0 筆不符；只有 10-12～10-19 有值，10-20 為 NULL |
| FUNDAMENTALS | DATA_BLOCKED | — | 有 MOPS 實際公告日的期別 12,790 筆（只到 FY2025），其餘 21,830 筆用法定截止日估計。依 OD-4，用截止日估計者不得判 DATA_PASS |
| MONTHLY_REVENUE | DATA_BLOCKED | — | 沒有官方來源與公告時間，只能用次月 10 日估計 |

單位：T86 是股數，與 MySQL 的 `foreign_buy`、`trust_buy`、`dealer_buy` 相同；`margin_balance` 是張。研究期外資的逐股吻合率為 86.7%；145 個共同日期中有 117 個日期 100% 吻合，其餘主要是整日存成 0。

## 2. 官方資料取得（OD-6）

- 來源：`https://www.twse.com.tw/rwd/zh/fund/T86`（`selectType=ALLBUT0999`）、`https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN`（`selectType=ALL`）。
- 頻率：每 3 秒最多 1 個請求，總共 330 個請求；已快取的檔案不重抓、也不覆寫。
- 快取：`artifacts/official_chip_cache/twse/`（51 MB，被 `.gitignore` 忽略，不進 Git）。每個檔案的 URL、擷取時間（UTC）與 SHA-256 記錄在 `official_chip_manifest.json`。
- 欄位定義：外資 = 外陸資買賣超股數（不含外資自營商）；投信 = 投信買賣超股數；自營商 = 自營商買賣超股數（自行買賣加避險）；融資 = 融資今日餘額。外資自營商另存一欄，不併入外資。這和 MySQL 的口徑一致（10 月外資 100% 吻合）。
- 研究快照：`research_snapshot_official_chips.parquet`，以 native7 快照為底，把四個籌碼欄位換成官方值，官方沒有的保留 NULL。MySQL 原始資料未修改。
- TPEx：研究股票池只含上市，這一輪不需要 TPEx 籌碼，因此沒有下載，維持 DATA_BLOCKED。

## 3. 時間可得性

| 資料 | 可得時點 | 依據 |
|---|---|---|
| 行情、法人、融資 | 當日收盤後；訊號在收盤後產生，次一交易日開盤成交 | 官方日報 |
| 財報 | 有 MOPS 實際公告日者：公告日的下一個交易日起可用（`due < date`）；其餘用法定截止日，並標記 `availability_source=statutory_deadline` | `core/research/native_frame.py:52` |
| 月營收 | 次月 10 日之後 | 法定上界，屬估計 |

## 4. 模型訓練資料（OD-3）

訓練截止日（2026-05-20）以前的 1,437,537 筆資料中，`dealer_buy`、`margin_balance` 全部是 NULL，`dealer_buy_vol` 也全部是 0。7 個模型的 `dealer_ratio_z` 重要性都是 0，`chip_score_z` 則在 0.017～0.031 之間。這顯示模型在訓練時，自營商與融資兩個分量是固定的中性值，`chip_score` 只隨外資與投信變動。如果把正式公式套用到現在真實的自營商與融資資料，得到的是另一個特徵。訓練當時的資料表結構沒有記錄，因此這是推論，不是證明。7 個模型也都沒有記錄 `training_data_end`。所以模型評分維持 MODEL_BLOCKED，不重新訓練，也不補 0。

## 5. 解除條件

| 缺口 | 需要什麼 |
|---|---|
| MySQL 法人錯誤（整日 0） | 以官方 T86 修復爬蟲與資料庫（寫入正式 DB 需要 Owner 核准，OD-9） |
| 自營商／融資歷史 | 研究期已改用官方資料；正式 DB 需要回補（同 OD-9） |
| 財報 2026Q1 以後、月營收 | 擷取 MOPS 的實際公告時間（OD-12） |
| 上櫃 | 官方 TPEx 行情、日曆、籌碼的快取與認證（OD-8） |
| 模型 | 補上 `training_data_end`；在 Owner 核准下，以認證後的籌碼資料重新訓練（OD-3a／3b） |
| 日曆 | 把官方 MI_INDEX 快取延伸到 2026-07-28 之後 |
