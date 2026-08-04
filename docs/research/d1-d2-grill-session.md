# D1／D2 因子研究 MVP — 設計訪談紀錄

> 建立日期：2026-07-28
> 狀態：**訪談已完成（D-01～D-18）；範圍已於 2026-07-30 由使用者擴大**
>
> **後續文件**：`openspec/changes/2026-07-30-factor-research-mvp/`
> 使用者於 2026-07-30 決定將**多資料來源（TWSE + yfinance）、還原股價、官方成交金額**納入同一個 change。
> **D-03（不建資料來源 pipeline）已被推翻**；D-07／D-09／D-11／D-14／D-17／D-18 有擴充。
> 完整變更對照見該 change 的 `design.md` §19。本檔保留為決策理由的原始紀錄，**不再是最新範圍的依據**。

---

## 1. 已確認事實（由 repo 直接查證，非決策）

### 1.1 行情資料

| 事實 | 證據 |
|---|---|
| 主行情表為 `daily_market_data`，屬 `_ALLOWED_TABLES` 白名單 | `core/db_helper.py:24-30` |
| 欄位：`trade_date, stock_id, open_price, high_price, low_price, close_price, volume` 為必要欄位 | `core/db_helper.py:1141`（`get_stock_history` required_columns） |
| 選用欄位：`ma5, ma20, ma60, rsi, bias, chip_score, foreign_buy, trust_buy, dealer_buy` | `core/db_helper.py:1142-1153` |
| `volume` 來源為 TWSE「成交股數」（單位＝股，非張、非金額） | `jobs/update_database.py:142`、`core/mcp_client.py:116` |
| **無 `amount`／成交金額欄位** | 全 repo grep `成交金額｜trade_value｜turnover` 僅命中 `core/backtest/metrics.py:72`（回測換手率，非行情） |
| **無 VWAP 欄位、無 VWAP 來源** | 全 repo grep `vwap` 零命中 |
| **無還原股價（adjusted price）** | 全 repo grep `adj_close｜adjusted` 零命中；`update_database.py` 直接落原始 OHLC |
| **無上市／下市／停牌／處置狀態表** | `_ALLOWED_TABLES` 內無任何 listing status 表 |
| 資料寫入為 `REPLACE INTO`（逐列） | `core/db_helper.py:1227-1275` |
| 指標欄位可動態 `ALTER TABLE ADD COLUMN ... FLOAT DEFAULT 0` | `core/db_helper.py:1384-1419` |

### 1.2 現有股票池規則

| 事實 | 證據 |
|---|---|
| `is_common_stock_id()`：僅接受 4 位純數字代號，且排除 `03`、`08` 開頭 | `core/db_helper.py:33-35, 766-770` |
| 因此 ETF（`0050` 為 4 位 → **會被納入**；`00632R` 等 6 位 → 被排除）語意不一致，需釐清 | `core/db_helper.py:766` |
| 回測 `find_candidates` 另外硬編碼 `close_price > 10 AND close_price < 500` | `core/backtest/runner.py:667-673` |
| 已有 OpenSpec capability `common-stock-universe-filter` | `openspec/specs/common-stock-universe-filter/` |
| 股票池為「每日由當日 `daily_market_data` 有資料者」隱含決定，無獨立 universe 表 | `core/backtest/runner.py:665` |

### 1.3 現有指標（可重用／可能重複）

| 函數 | 位置 | 與候選因子的關係 |
|---|---|---|
| `calculate_rsi` | `core/calc_indicators.py:87` | — |
| `calculate_macd` | `core/calc_indicators.py:108` | — |
| `calculate_atr` | `core/calc_indicators.py:140` | `natr_14d` 依賴；使用 **EWM(span=period, adjust=False)**，非 Wilder RMA |
| `calculate_natr` | `core/calc_indicators.py:172` | **已存在**：`(ATR/Close)*100`，並 `fillna(0).clip(0, 50)` → 與研究用因子語意衝突（研究不應以 0 填補缺值） |
| `calculate_std_20` | `core/calc_indicators.py:196` | 與 `realized_vol_20d` 相關但非同一定義（價格標準差 vs 報酬標準差） |
| `calculate_kd` / `calculate_kd_full` | `core/calc_indicators.py:212/233` | `range_position` 與 KD 的 %K 概念高度重疊 |
| `calculate_bb_width` | `core/calc_indicators.py:265` | — |
| `calculate_bias` | `core/calc_indicators.py:293` | — |
| `calculate_ratio_features` | `core/calc_indicators.py:300` | 已含 `volume_ratio = volume / rolling(20, min_periods=1).mean()`，並 `clip(0,5)` → 與 `volume_ratio_20d` 重疊但有 clip |
| `calculate_cross_sectional_zscore` | `core/calc_indicators.py:394` | 已有橫斷面標準化，**但缺值以 0 填補**（研究語意上不可接受） |
| `build_multi_factor_matrix` | `core/calc_indicators.py:441` | 已有 24 欄多因子矩陣 + `_z` 欄位；為線上選股用途 |
| **不存在**：`rank_cs`、`rank_ts`、`ts_corr`、`ts_max/min`、Amihud、動量算子 | grep 確認 | D2 需新建 |

### 1.4 回測與時間語義（現況）

| 事實 | 證據 |
|---|---|
| 訊號時間＝成交時間＝**同日收盤價** | `core/backtest/runner.py:983-988`（`self.buy(sid, data['close_price'], date_str)`） |
| 憲法第十三條禁止「使用當日收盤資訊並假設能以同一收盤價成交，除非規格明確允許」→ 現況屬已存在的例外 | `openspec/project.md` 第十三條 |
| 滑價：`random.uniform(0, max_slippage)`，需 seed | `core/backtest/execution.py`、`runner.py:57` |
| 成本模型：`CostModel(fee_rate, tax_rate, minimum_fee=20.0)` | `core/backtest/costs.py` |
| 績效指標統一於 `core/backtest/metrics.py`，19 個指標，不可計算時回 `MetricValue(None, reason)` | `core/backtest/metrics.py:87` |

### 1.5 驗證模組現況

| 事實 | 證據 |
|---|---|
| `core/validation/` 是**策略穩定性驗證**（bootstrap／walk_forward／rolling／param_scan／cost_sensitivity／split／correlation），**不是資料契約驗證** | `core/validation/*.py` |
| 全 repo **沒有**行情資料契約驗證器 | grep 確認 |

### 1.6 測試與工具鏈

| 事實 | 證據 |
|---|---|
| pytest 已設定，`testpaths = test`，`pythonpath = .`，markers：`slow / integration / unit / allow_real_backtest_persistence` | `pytest.ini` |
| 測試分層：`test/`（整合）、`test/unit/`、`test/characterization/` | 目錄結構 |
| autouse fixture 阻擋真實 `save_backtest_results()` 落庫 | `test/conftest.py:26-46` |
| **無 CI**：`.github/workflows/` 不存在 | `ls .github/` |
| **無 lint／type-check 設定**：無 ruff／mypy／flake8／black 設定或依賴 | requirements 檢查 |
| 可用依賴：`pandas 2.2.3`、`numpy 1.26.4`、`pydantic 2.11.10`、`scikit-learn`、`xgboost` | `requirements.runtime.txt` |
| **無 scipy／statsmodels／pandera／great_expectations** | `requirements.runtime.txt` |

### 1.7 OpenSpec

| 事實 | 證據 |
|---|---|
| OpenSpec CLI v1.3.0 已安裝 | `openspec --version` |
| `schema: spec-driven` | `openspec/config.yaml` |
| **Change 目錄必須為 `YYYY-MM-DD-<change-name>`**，tasks 標題亦須日期前綴 | `AGENTS.md`、`openspec/project.md` 第十七條 |
| 既有 30+ 個 capability spec 於 `openspec/specs/` | `ls openspec/specs` |

> **偏差記錄**：使用者提示中的 `openspec/changes/factor-research-mvp/` 不符合本專案第十七條的日期前綴慣例。實際將採用 `openspec/changes/2026-07-28-factor-research-mvp/`（待決策 G 確認）。

---

## 2. 已確認決策

### D-01 研究流程入口：CLI 優先，但輸出必須可回饋既有前端

- **決策**：本週研究流程於**終端（CLI）**執行與驗證。但研究產出的**最終結果必須能回饋到現有前端**（Web Dashboard 或 LINE），不得是只存在於終端的一次性輸出。
- **日期**：2026-07-28
- **推論的設計約束**：
  1. 因子輸出需要**穩定、具名、可被第三方讀取的 schema**（不能只是 `print` 或臨時 DataFrame）。
  2. 但「回饋前端」是**最終目標，非本週交付**——本週交付的是讓它成為可能的資料契約。
  3. Web／LINE 的實際 route 與訊息模板**明確延後**。
  4. 憲法第九條依賴方向維持：因子模組不得 import Flask／LINE SDK。

---

### D-02 因子輸出落點：只寫 `artifacts/`，本週不碰資料庫

- **決策**：因子輸出寫入 `artifacts/factors/` 的檔案，**不新增 DB 表、不修改 `daily_market_data`**。
- **日期**：2026-07-28
- **被拒方案**：
  - (c) 在 `daily_market_data` 加因子欄位 → `ensure_indicator_columns()` 一律 `FLOAT DEFAULT 0`（`core/db_helper.py:1408`），缺值會被偽裝成 0，違反憲法第十四條；且會把未驗證數字混入正式選股熱表。
  - (b) 新增 `factor_values` 表 → 在零個已驗證因子的情況下做 schema 決策，過早。
- **推論的設計約束**：
  1. 本週真正的交付物是**欄位契約**，不是儲存媒介。契約穩定 → 日後換成 DB writer 是局部替換。
  2. 格式使用 **CSV**（`pyarrow 18.1.0`／`scipy 1.13.1` 雖在環境中，但**未列入 `requirements.runtime.txt`**，而 `test/test_environment_pins.py` 會檢查釘選 → 使用它們等同新增依賴）。
  3. `artifacts/` 已 gitignore（`.gitignore:41-46`），不會污染版控。

### D-03 價格基準：使用原始未還原價，並以三項強制條件揭露限制

- **決策**：本週使用 `daily_market_data` 的**原始未還原成交價**。不建立還原股價 pipeline。
- **日期**：2026-07-28
- **使用者補充**：「就根據研究的完整性配置」→ 以下三項條件屬**強制**，不是建議。
- **強制條件**：
  1. **C1**：每個受影響因子的 metadata 必須含 `price_basis = "raw_unadjusted"`，寫在資料／registry 中，不得只寫在 README。
  2. **C2**：D1 資料驗證器必須包含 `suspicious_gap` 檢查——單日 `|close / prev_close - 1| > 20%`（超過台股 10% 漲跌幅上限的兩倍）即標記。用途是抓除權息、減資與資料錯誤，成本極低。
  3. **C3**：`docs/` 必須明載：**在還原股價到位前，所有 Rank IC 與分位數結果皆為下限估計，不得作為上線決策依據。**
- **受影響因子（8／12）**：`momentum_20d`、`momentum_60d`、`momentum_12_1`、`near_high_252d`、`reversal_5d`、`realized_vol_20d`、`natr_14d`、`price_volume_corr_20d`
- **被拒方案**：
  - (b) 以 `yfinance` 建還原股價 ingestion → 實際成本遠高於表面（`.TW`／`.TWO` 代號映射、1900 檔 rate limit、與現有表對帳、台股還原品質不穩），會吃掉整個 D1；且違反憲法第十八條（新資料源與資料契約混在同一任務）。
  - (c) 刪除受污染因子 → 動量是價量研究主軸，砍掉本週目的即消失。
- **Revisit trigger**：任一因子的 Rank IC 通過門檻、準備進入上線決策時，還原股價成為前置條件。

### D-04 時間語義：研究 pipeline 採 T+1 執行，與既有回測刻意分歧

- **決策**：研究 pipeline 的 `signal_time = T 收盤`、`execution_time = T+1`。Forward return 自 T+1 起算。
- **日期**：2026-07-28
- **明確非目標**：**不修改既有回測的成交時點**。`core/backtest/runner.py:988` 的「同日收盤成交」維持原狀，受 `test/characterization/test_trade_sequence_regression.py` 保護，且 AGENTS.md 要求結構重構不得改變成交時點。
- **兩者刻意分歧**，差異必須寫進文件，不得視為 bug。
- **被拒方案**：
  - (a) 沿用同日收盤 → Rank IC 是橫斷面全市場計算，等同假設能在收盤瞬間吃下全市場收盤價；反轉類因子（`reversal_5d`）虛胖最嚴重（訊號本質是「今天跌很多」，卻用今天收盤價買進）。
  - (c) 兩者都算並比較差異 → 在尚未有任何確認有效因子前比較執行時點差異，沒有資訊價值。

#### 時間語義詞彙表（本次確認）

| 名詞 | 定義 | 本週是否實作 |
|---|---|---|
| `trade_date` | 該筆 OHLCV 所屬交易日（T） | ✅ 既有欄位 |
| `available_at` | T 日 14:30（收盤後） | ❌ **推導值，不建欄位** |
| `signal_time` | T 日收盤後；因子只能使用 `trade_date <= T` 的資料 | ✅ 以測試強制 |
| `execution_time` | T+1 | ❌ 為 forward return 階段預留，D1／D2 不實作 |
| 時區 | 一律視為 `Asia/Taipei` naive date，**不做 tz 轉換**（沿用 `core/db_helper.py:894` `normalize_date_str`） | ✅ |

### D-05 研究股票池資產類型：僅普通股，`^[1-9]\d{3}$`

- **決策**：研究股票池僅含**上市櫃普通股**，代號正則寫死 `^[1-9]\d{3}$`（排除所有 `00` 開頭 ETF 與債券 ETF）。
- **日期**：2026-07-28
- **背景：repo 內三套互相矛盾的規則**

  | 位置 | 規則 | 實際保留 |
  |---|---|---|
  | 寫入端 `jobs/update_database.py:620` | `^([1-9]\d{3}\|00)` | 個股 ＋ **全部 ETF** |
  | 策略端 `core/strategies/base.py:271` | 同上 | 同上 |
  | `core/db_helper.py:772` `filter_common_stock_universe` | `^\d{4}$` 且排除 `03`/`08` | 個股 ＋ **4 碼 ETF（`0050`、`0056`）**，排除 5–6 碼 ETF |

  → 第三者的 docstring 聲稱「排除 ETF」，但 `0050` 為 4 碼且不以 03/08 起始，**實際被保留**。此為既有隱性錯誤。

- **被拒方案**：
  - (b) 普通股＋ETF → ETF 為一籃子，波動天生偏低，會系統性佔據 `realized_vol_20d`／`natr_14d` 分布最低分位；槓桿／反向 ETF（`00631L`、`00632R`）則佔據另一端。且 ETF 無月營收／財報／籌碼資料，未來每一步都要寫例外分支。
  - (c) 沿用 `filter_common_stock_universe` → 將既有 bug 當成研究基準。
- **明確非目標**：**本週不修 `filter_common_stock_universe`**。它綁定 OpenSpec capability `common-stock-universe-filter` 與既有測試。研究模組自帶 universe 函數；該 bug 記入 `docs/refactor/remaining_tech_debt.md`。
- **已排除的非問題**：**興櫃**從未進入寫入端正則，`daily_market_data` 中不存在興櫃資料——這是事實，不是選擇。

### D-06 股票池構成：每日動態重算

- **決策**：對每個交易日 T，股票池 = 「T 日於 `daily_market_data` 有資料」∩「`^[1-9]\d{3}$`」∩「流動性／價格門檻（待 Q7）」。**不使用固定清單。**
- **日期**：2026-07-28
- **被拒方案**：(b) 固定清單 → 以最新清單回推歷史，等同預先剔除所有下市／被併購標的，正是動量因子最該捕捉的負報酬尾端，會使 Rank IC 系統性偏高且**無法量測偏誤大小**。
- **強制降級揭露（延續 D-03 精神）**：
  - **C4**：D1 universe 函數必須輸出**每日檔數序列** artifact。若該序列隨時間單調遞增至今日，即代表下市股票歷史列已被清除；`docs/` 必須載明「存活者偏差未消除，Rank IC 為**上限**估計」。
- **重要提醒（兩個偏誤方向相反、不互相抵消）**：
  - D-03 無還原股價 → Rank IC 偏**低**
  - 存活者偏差 → Rank IC 偏**高**
  - 兩者為獨立未知量，**必須各自揭露**，不得宣稱「大致抵消」。

### D-07 股票池門檻組合

- **決策**：
  | 門檻 | 值 | 理由 |
  |---|---|---|
  | 流動性 | `rolling_20d_mean(close_price × volume) ≥ 20,000,000 TWD` | 使用者確認 |
  | 最低價格 | `close_price >= 10` | 台股跳動單位在 10 元以下為 0.01，報酬率被量化，`reversal_5d` 會產生假訊號 |
  | 最低上市歷史 | **不設獨立門檻** | 由各因子 `min_periods` 自然決定；歷史不足者該檔該日為 NaN |
  | 停牌／處置 | **不特別處理**，僅以 `volume > 0` 排除當日無成交 | repo 內無處置股／全額交割股資料（grep 確認）；建新資料源違反本週邊界 |
- **日期**：2026-07-28
- **關鍵事實修正**：使用者原本提問的「成交股數 vs 成交金額」中，**成交金額欄位不存在**。只能以 `close_price × volume` 近似。此近似在日內波動大時失真（真實成交金額基於每筆成交價），但方向與量級正確。
- **選金額而非股數的理由**：股數門檻對高價股不公平——台積電 3 萬張是流動性充沛，低價股 3 萬張可能僅 300 萬元。金額才可比。
- **強制揭露**：
  - **C5**：`docs/` 必須載明「流動性以 `close × volume` 近似，非真實成交金額」與「處置股分盤集合競價會扭曲當日價格，未處理」。
- **未驗證**：門檻套用後的實際檔數（估計 800–1200 檔）**尚未驗證，DB 未啟動**。D1 實作時必須實測並記錄；若檔數低於 400，分位數分析（尤其十分位）將不穩定，須回頭調整門檻。

### D-08 因子資料形狀：內部寬表運算，輸出長表

- **決策**：(c) 內部以**寬表**運算，輸出轉**長表**。
- **日期**：2026-07-28
- **具體形狀**：
  ```python
  # 輸入：D1 產出的驗證後長表 [trade_date, stock_id, open/high/low/close_price, volume]
  # 內部：dict[str, DataFrame]，key ∈ {'open','high','low','close','volume'}
  #       每張 DataFrame: index=trade_date (DatetimeIndex, 遞增), columns=stock_id (排序)
  # 算子：純函數，wide DataFrame -> wide DataFrame，形狀不變
  # 輸出：長表 [trade_date, stock_id, factor_name, value]
  ```
- **理由**：
  1. 時序算子在寬表上原生：`close.rolling(20).mean()`。長表版本 `groupby(...).transform(lambda x: x.rolling(20).mean())` **未先 sort 會靜默算錯**，不報錯只讓 IC 變噪音——量化研究最常見的隱形 bug。
  2. 橫斷面算子在寬表上是 `df.rank(axis=1)`，不需 groupby，快一個數量級。
  3. `price_volume_corr_20d` 這類雙序列算子，寬表上是 `close_ret.rolling(20).corr(vol_change)`；長表版本難寫難測。
  4. 輸出必須長表：`factor_name` 這一維在寬表無法自然表達（會退化成一堆檔案），且 D-01 要求最終能回饋前端。
- **強制約束**：
  - **C6**：pivot 成寬表時**必須斷言 `(trade_date, stock_id)` 唯一**，不得使用 `pivot_table` 靜默聚合。
  - 證據：`core/db_helper.py:1182` 的 `get_stock_history` 內含 `drop_duplicates(subset=['trade_date','stock_id'])`，**暗示重複列曾實際發生過**。此檢查應由 D1 驗證器攔截。

### D-09 第一批因子：刪除 `vwap_gap`，保留近似 `amihud_20d`，補入 `overnight_gap_20d`

- **決策**：
  - ❌ **刪除 `vwap_gap`**
  - ✅ **保留 `amihud_20d`**，以 `close × volume` 近似成交金額，metadata 標記為近似
  - ➕ **補入 `overnight_gap_20d`** = `mean(open / prev_close - 1, 20)`
  - → 第一批仍為 **12 個因子**
- **日期**：2026-07-28
- **刪除 `vwap_gap` 的理由**：日頻唯一可用的 VWAP 代理是 typical price `(H+L+C)/3`，而 `close/((H+L+C)/3) - 1` **本質上就是收盤價在當日區間中的相對位置——即 `range_position` 本身**。會產出一個與 `range_position` 相關係數 >0.9 的雙胞胎，卻掛著宣稱自己是 VWAP 的名字，違反憲法第十二條「策略名稱不得宣稱程式中不存在的條件」。
- **保留 `amihud_20d` 的理由**：Amihud 非流動性 = `mean(|ret| / 成交金額)`。以 `close × volume` 近似的誤差來自「以收盤價代替當日均價」，屬**幾個百分點**量級；而 Amihud 跨股票差異達**數個數量級**。誤差遠小於訊號，近似成立。且流動性維度為其餘 11 個因子完全未覆蓋。
- **補入 `overnight_gap_20d` 的理由**：`open_price` 目前**未被任何候選因子使用**；隔夜跳空捕捉盤前資訊反應，與其他因子維度不重疊。

### D-10 算子實作：研究模組自帶嚴格 NaN 語義的算子，不重用既有指標函數

- **決策**：(b) 研究模組實作自己的算子，採**嚴格 NaN 語義**。**不重用** `core/calc_indicators.py` 的既有指標函數。
- **日期**：2026-07-28
- **既有函數不可重用的原因（全部把缺值填成 0）**：

  | 函數 | 位置 | 對研究致命處 |
  |---|---|---|
  | `calculate_natr` | `core/calc_indicators.py:190` | `natr.fillna(0).clip(0,50)` — 前 14 天無資料 → 波動度變 **0** → 被排入「最低波動」分位 |
  | `calculate_cross_sectional_zscore` | `:394,429` | 缺值填 0 → 無資料的股票變成「剛好在平均值」 |
  | `calculate_ratio_features` | `:324` | `rolling(20, min_periods=1)` → 只有 1 天資料也給出「20 日均量」 |
  | `calculate_atr` | `:167` | `ewm(span=period)` 而非 Wilder RMA（非錯誤，但與標準 ATR 定義不同） |

- **核心論證**：`fillna(0)` 在線上選股是「安全預設」（絕不回傳 NaN 讓下游炸掉），在研究中是**偽造資料**。上市第 3 天的股票會拿到 `natr = 0` 被排入最低波動分位，最終產出「低波動有超額報酬」的結論——而該結論**完全來自新股**。
- **Ponytail 相容性說明**：Ponytail 階梯的「重用既有元件」前提是**它解的是同一個問題**。線上選股要「絕不回傳 NaN」，研究要「絕不假造資料」，兩者需求相反，非重複實作。
- **被拒方案**：
  - (a) 重用並接受 `fillna(0)` → 分位數極端端點塞滿新股假 0，Rank IC 量測的是新股效應而非因子。
  - (c) 重用後在研究側把 0 還原為 NaN → **技術上不可能**。`natr = 0` 之後無法區分「真的低波動」與「缺值」，資訊已被銷毀。
- **代價評估**：12 個因子約需 8 個算子（`ts_mean`／`ts_std`／`ts_rank`／`ts_corr`／`ts_max`／`ts_min`／`rank_cs`／`delta`），每個 1–3 行寬表運算。

#### 缺值政策（本次確認）

| 情況 | 處理 |
|---|---|
| rolling `min_periods` | **等於完整窗長**，不足即 NaN |
| 除以 0 | 產生 `inf` → **一律轉 NaN** |
| 常數序列（分母 std = 0） | NaN，**不是 0** |
| NaN 傳播 | **一路傳到輸出**，輸出檔中即為空值 |
| 輸出檔中的 Inf | **禁止**，測試強制斷言 |
| `rank_cs` 遇 NaN | `rank(axis=1, pct=True)` 保持 NaN（pandas 預設行為，無需額外處理） |

### D-11 Factor Registry：module-level dict + frozen dataclass；`direction` 一律先設 0

- **決策**：(b) 一個 module-level `dict[str, FactorSpec]`，`FactorSpec` 為 `@dataclass(frozen=True)`。**不建 class、不用 Singleton、不用 decorator。**
- **日期**：2026-07-28
- **最低 metadata 欄位**：
  ```python
  @dataclass(frozen=True)
  class FactorSpec:
      name: str
      fn: Callable                    # dict[str, wide DF] -> wide DF
      required_columns: tuple[str, ...]
      lookback: int
      direction: int                  # +1 / -1 / 0
      price_basis: str                # 'raw_unadjusted'（D-03 條件 C1）
      description: str
  ```
  各欄位的實際用途：`required_columns` → 執行前擋缺欄；`lookback` → 決定需讀多少歷史；`direction` → Rank IC 正負號解讀；`price_basis` → D-03 強制條件 C1。
- **被拒方案**：
  - (d) 仿 `StrategyManager`（`core/strategy_manager.py:34`）建 `FactorManager` → Singleton／lazy import／legacy alias 解析／設定檔持久化，因子研究**一項都不需要**；違反憲法第十條與 Ponytail。
  - (c) decorator 註冊 → 要求「模組被 import 過」才註冊，需靠 `__init__.py` 逐一 import，否則 registry **靜默地少東西**，除錯成本高。
  - (a) 純 `dict[str, callable]` → 缺 metadata。
- **`direction` 決策：第一批 12 個因子 `direction` 一律為 `0`（方向未知，待研究確認）。**
  - `0` 表示「未知」，不另開 `research_required` 字串欄位——`0` 在下游計算中自然中性，且強迫面對「尚不知方向」的事實。
  - **理由**：台股的動量效應在學術文獻上顯著弱於美股，短期反轉反而更強。任何 `+1`／`-1` 的預先指派都只是**先驗假設而非已知事實**，而驗證它正是本週的目的。**讓 Rank IC 自己說話。**

### D-12 第一批 12 個因子的精確定義

- **決策**：以下定義為 D2 的實作契約。所有運算在寬表上進行（`open`／`high`／`low`／`close`／`volume` 皆為 wide DataFrame，index=trade_date，columns=stock_id）。
- **日期**：2026-07-28
- **所有因子 `direction = 0`、`price_basis = 'raw_unadjusted'`**（D-11、D-03）

| # | 因子 | 公式 | lookback | required_columns |
|---|---|---|---|---|
| 1 | `momentum_20d` | `close/close.shift(20) - 1` | 21 | close |
| 2 | `momentum_60d` | `close/close.shift(60) - 1` | 61 | close |
| 3 | `momentum_12_1` | `close.shift(21)/close.shift(252) - 1` | 253 | close |
| 4 | `near_high_252d` | `close/close.rolling(252).max() - 1` | 252 | close |
| 5 | `reversal_5d` | `close/close.shift(5) - 1` | 6 | close |
| 6 | `volume_ratio_20d` | `volume/volume.rolling(20).mean()` | 20 | volume |
| 7 | `price_volume_corr_20d` | `ret.rolling(20).corr(log(volume).diff())`，`ret = close.pct_change()` | 22 | close, volume |
| 8 | `range_position` | `(close - low)/(high - low)`，**0～1，當日盤中** | 1 | high, low, close |
| 9 | `realized_vol_20d` | `close.pct_change().rolling(20).std(ddof=1) * sqrt(252)` | 21 | close |
| 10 | `natr_14d` | `ATR14/close`，ATR 採 **Wilder RMA**，**不乘 100** | 15 | high, low, close |
| 11 | `amihud_20d` | `(abs(ret)/(close*volume)).rolling(20).mean()`，**不縮放** | 21 | close, volume |
| 12 | `overnight_gap_20d` | `(open/close.shift(1) - 1).rolling(20).mean()` | 21 | open, close |

**關鍵定義選擇與理由：**

- **`momentum_12_1` = 252/21**：`shift(21)` 至 `shift(252)`，跳過最近一個月以避開短期反轉污染長期動量。標準定義。
- **`range_position` 採當日盤中、0～1**：0～1 語義自明（收盤價落在當日區間的百分位）。**採當日而非 20 日**，是為了與 `core/calc_indicators.py:212` 的 `calculate_kd`（本質為 20 日 Stochastic %K）區隔，避免重蹈 `vwap_gap` 雙胞胎覆轍。
- **NATR 不乘 100**：橫斷面 rank 對縮放不變，乘 100 無益，反而會讓人誤以為與既有 `calculate_natr`（有 ×100、有 clip、有 fillna(0)）相同。**刻意不同值以避免混淆。**
- **Amihud 不縮放**：值約在 `1e-11` 量級，float64 足以承載。任何縮放常數皆為任意值。**單位寫入 metadata description。**
- **`price_volume_corr_20d` 採「報酬率 vs log 成交量差分」**：不用 `volume.pct_change()`，因成交量百分比變化尾部極重（爆量日動輒 +500%），會使相關係數被單日事件綁架；取 log 後差分穩定得多。

### D-13 `rank_cs` 與 `rank_ts` 延後至 D3

- **決策**：**D2 不實作 `rank_cs` 與 `rank_ts`。**
- **日期**：2026-07-28
- **理由**：核對 D-12 的 12 個因子，**無任何一個使用 `rank_cs` 或 `rank_ts`**。`rank_cs` 於 D3 計算 Rank IC 時才需要，`rank_ts` 可能永不需要。現在實作等同為零個使用者做設計；屆時才會知道 NaN 處理與是否 `pct` 該如何定義。

### D-14 測試策略與阻擋條件

- **決策**：阻擋條件共 **4 條**，全部為人工約定（本 repo **無 CI**、**無 lint**、**無 type check**）。
- **日期**：2026-07-28

| # | 阻擋條件 | 指令／方式 |
|---|---|---|
| **B1** | 新增的 D1／D2 測試 100% 通過 | `pytest test/unit/research/ -q` |
| **B2** | 既有測試**不得新增失敗**（與 baseline 比對，**不要求全綠**） | `pytest test/ -q` |
| **B3** | 因子輸出檔中**不得有 Inf**；NaN 比例須被記錄 | 由 B1 測試斷言 |
| **B4** | **look-ahead sentinel 測試通過** | 見下方 |

- **明確不阻擋**：lint、type check、覆蓋率門檻、CI 執行時間限制。本週不建立這些機制。

#### B4 look-ahead sentinel（使用者指定列為阻擋條件）

做法：將 fixture **最後 5 天的資料改為極端值**（如 `close = 999999`），重算全部因子，斷言**倒數第 6 天（含）以前的所有因子值完全不變**，浮點容差 `rtol=1e-9`／`atol=1e-12`（比照 AGENTS.md「Verification」段標準）。

> 此單一測試即可攔截因子引擎最致命的一類 bug。若只能保留一個測試，保留這個。

#### Golden fixture（僅一份）

`test/fixtures/research/` 下一份**手算可驗證**的小 CSV：**3 檔股票 × 260 個交易日**，數值刻意設計：

| 設計 | 驗證目標 |
|---|---|
| 一檔線性上漲 | 動量類因子的正確性可手算核對 |
| 一檔常數序列 | std=0 時回 **NaN 而非 0** |
| 一檔含缺值與零成交量 | 除零轉 NaN、NaN 傳播 |
| 260 天長度 | 剛好覆蓋 `momentum_12_1`（需 253）的邊界 |

- **明確反對：因子數值的 golden 快照測試。** 快照在公式改動時只會告訴你「數字變了」，不會告訴你「變得對不對」，最終導致習慣性更新 baseline——正是憲法第十五條禁止的「更新 baseline 而不說明行為差異」。**採用手算可驗證的斷言，不採用快照。**

### D-15 D1 驗證器失敗語義：FATAL／WARN 分級，分級寫死不可設定

- **決策**：(b) 規則分 `FATAL`／`WARN`。FATAL 中止 pipeline，WARN 記錄後繼續。**分級寫死於程式，不提供設定參數。**
- **日期**：2026-07-28
- **分級界線**：**「會讓後續運算靜默算錯的 → FATAL；只是資料長得奇怪的 → WARN」**

| 規則 | 級別 | 理由 |
|---|---|---|
| 缺必要欄位 | **FATAL** | 下游必炸 |
| `(trade_date, stock_id)` 重複 | **FATAL** | pivot 會靜默取平均或直接炸（C6） |
| 價格 ≤ 0 或為負 | **FATAL** | 報酬率成為無意義數字 |
| OHLC 關係錯誤（`high < low`、`close` 不在 `[low, high]`） | **FATAL** | 資料源出錯；`range_position` 會產出 >1 或 <0 |
| `volume` 為負 | **FATAL** | 不可能發生，發生即資料損壞 |
| `trade_date` 無法解析／非交易日 | **FATAL** | 時序對齊會錯 |
| `suspicious_gap` >20%（C2） | **WARN** | 多數為除權息，屬正常現象（D-03） |
| `volume == 0` | **WARN** | 停牌／無成交，由股票池門檻自然排除（D-07） |
| 單日股票數相對前一日變動 ±30% | **WARN** | 可能為抓取不完整，也可能為連假 |

- **被拒方案**：
  - (a) 任一違反即 `raise` → 除權息是正常現象，會使 pipeline 永遠跑不完，三天內該檢查就會被註解掉。
  - (c) 純報告永不中止 → 重複鍵（C6）若僅 WARN，pivot 仍會炸，錯誤訊息還是 pandas 的，等於白寫。
- **「不可設定」的理由**：一旦 FATAL／WARN 可由參數調整，第一次卡住時就會被降級為 WARN 且永不調回。寫死才具守門效果。此處不需要 config（Ponytail）。
- **強制條件**：
  - **C7**：WARN 必須輸出為 artifact（`artifacts/factors/validation_report.csv`），**不得僅 print**。C2／C4／C5 要求的「揭露限制」若僅存在於終端捲動訊息中，等同未揭露。

### D-16 OpenSpec：單一 change，D1 與 D2 同屬一個變更

- **決策**：建立**單一** OpenSpec change，D1 與 D2 皆包含在內。**不拆成兩個 change。**
- **日期**：2026-07-29
- **目錄名稱**：`openspec/changes/2026-07-29-factor-research-mvp/`
  - > **偏差記錄**：使用者原始提示中的 `openspec/changes/factor-research-mvp/` 不符本專案慣例。`AGENTS.md` 與憲法第十七條要求 change 目錄為 `YYYY-MM-DD-<change-name>`，既有 30+ 個 change 皆遵守。日期前綴採**提案建立日**（2026-07-29），非訪談起始日。
- **理由**：
  1. **D1 沒有獨立驗收價值**——沒有因子去消費資料契約，就無法判斷契約定得對不對。拆開後 D1 的驗收會退化成「測試通過」，屬自我證明。
  2. 憲法第十八條禁止的是「**混合不同性質的工作**」（結構重構＋策略調整、DB 遷移＋UI 改版）。D1 與 D2 是**同一件事的兩個階段**。
  3. 拆開會製造假邊界：D1 定的欄位契約，D2 一寫因子必然要調整。同 change 內調整屬正常迭代；跨 change 調整則變成「修改已核准規格」。
  4. `tasks.md` 的日期前綴（`[2026-07-29]` D1／`[2026-07-30]` D2）已足以表達階段分界，無須用目錄結構重複表達。

### D-17 Capability 命名加 `research-` 前綴；不建 ADR，決策全寫入 `design.md`

- **決策**：
  1. 4 個 capability spec **全部加 `research-` 前綴**。
  2. **不建立 `docs/adr/`**，D-01～D-17 全部寫入 OpenSpec `design.md`。先前建立的空目錄 `docs/adr/` 予以移除。
- **日期**：2026-07-29
- **capability 清單**：

  | capability | 涵蓋決策 |
  |---|---|
  | `research-market-data-contract` | 欄位契約、時間語義（D-04）、價格基準（D-03） |
  | `research-data-validation` | FATAL／WARN 規則（D-15）、C2／C6／C7 |
  | `research-universe` | 每日動態股票池（D-05、D-06、D-07）、C4／C5 |
  | `research-factor-engine` | 資料形狀（D-08）、算子與缺值政策（D-10）、Registry（D-11）、12 因子（D-12）、rank 延後（D-13） |

- **加前綴的理由**：`openspec/specs/` 已存在 `common-stock-universe-filter`（線上選股股票池）。不加前綴則半年後無法區分研究側與線上側——而 D-04（T+1 vs 同日收盤）、D-05（排除 ETF vs 保留 ETF）、D-10（嚴格 NaN vs `fillna(0)`）三個決策的核心**正是刻意分歧**。前綴使此事成為名稱的一部分。
- **不建 ADR 的理由**：憲法第十九條指定重大架構決策記錄於 OpenSpec `design.md`；本 repo 從無 `docs/adr/`。同一批決策記錄於兩處必然不同步。使用者原始訪談大綱為通用模板，此處與專案慣例衝突，**以專案慣例為準**。

### D-18 檔案放置：新增 `core/research/`，4 個檔案

- **決策**：(a) 新增 `core/research/` 套件，共 4 個檔案。
- **日期**：2026-07-29
- **檔案結構**：
  ```
  core/research/
  ├── __init__.py
  ├── market_data.py     # 載入 + 驗證 + FATAL/WARN（D-15）+ 長轉寬（D-08）
  ├── universe.py        # 每日動態股票池（D-05～D-07）
  └── factors.py         # 8 個算子 + FactorSpec + FACTOR_REGISTRY + 12 因子（D-10～D-12）

  jobs/run_factor_research.py   # CLI 入口（D-01），僅參數解析與呼叫，零業務邏輯
  test/unit/research/           # 測試
  test/fixtures/research/       # golden fixture（D-14）
  ```
- **被拒方案**：
  - (c) 放進既有 `core/validation/` → 該目錄是**策略穩定性驗證**（bootstrap／walk_forward／param_scan），與資料契約驗證是兩件事，僅名稱相撞。放入會製造「名稱相同、責任不同」的混淆點。
  - (b) 單一 `core/research.py` → 載入＋驗證＋股票池＋8 算子＋registry＋12 因子約 500–700 行，涵蓋三個清楚不同的責任，觸及憲法第十條的檢查門檻。
- **刻意不拆的部分（憲法第十條）**：
  - **算子不獨立成 `operators.py`**：8 個算子約 30 行，且**僅 `factors.py` 使用**。憲法第十條明列「新檔案只被單一位置使用，且沒有獨立概念」不應拆檔。待 D3 的 Rank IC 也需使用時再拆——屆時才存在第二個使用者。
  - **`FactorSpec` 不獨立成 `types.py`**：7 欄位 dataclass，與 registry 放一起最易讀。
  - **驗證不獨立成 `validation.py`**：驗證規則是資料載入的一部分，分開將使「讀資料」這個簡單流程需跨兩檔理解。

---

## 3. 待決問題

| # | 主題 | 問題 | 狀態 |
|---|---|---|---|
| ~~Q1~~ | A. MVP 邊界 | 研究流程入口 | ✅ 已決（D-01） |
| ~~Q2~~ | B. 資料落點 | 因子輸出落點 | ✅ 已決（D-02） |
| ~~Q3~~ | B. 價格語義 | 還原股價處理 | ✅ 已決（D-03） |
| ~~Q4~~ | B. 時間語義 | signal／execution 時點 | ✅ 已決（D-04） |
| ~~Q5~~ | C. 歷史股票池 | 資產類型 | ✅ 已決（D-05） |
| ~~Q6~~ | C. 歷史股票池 | 構成方式 | ✅ 已決（D-06） |
| ~~Q7~~ | C. 歷史股票池 | 門檻組合 | ✅ 已決（D-07） |
| ~~Q8~~ | D. 因子資料模型 | 資料形狀 | ✅ 已決（D-08） |
| ~~Q9~~ | E. 第一批因子 | `vwap_gap` / `amihud_20d` 存廢 | ✅ 已決（D-09） |
| ~~Q10~~ | D + H | 算子重用與缺值政策 | ✅ 已決（D-10） |
| ~~Q11~~ | D + H | Registry 形式與 metadata | ✅ 已決（D-11） |
| ~~Q12~~ | E. 第一批因子 | 12 個因子精確定義 | ✅ 已決（D-12、D-13） |
| ~~Q13~~ | F. 測試與可靠性 | 阻擋條件與 fixture | ✅ 已決（D-14） |
| ~~Q14~~ | F + B | 驗證器失敗語義 | ✅ 已決（D-15） |
| ~~Q15~~ | G. OpenSpec | change 拆分方式 | ✅ 已決（D-16） |
| ~~Q16~~ | G. OpenSpec | 命名前綴與決策記錄位置 | ✅ 已決（D-17） |
| ~~Q17~~ | H. Ponytail | 檔案放置與模組劃分 | ✅ 已決（D-18） |

**決策樹已走完，無待決決策。** 剩餘為實作前必須查證的事實（F-01～F-03，需啟動 MySQL）。
| ~~F-04~~ | 事實待補 | 既有測試 baseline | ✅ 已查證，見下方 R8 |
| F-01 | 事實待補 | `daily_market_data` 實際歷史深度（最早 trade_date、交易日數、股票數） | **DB 未啟動，無法查證** |
| F-02 | 事實待補 | `daily_market_data` 是否保留已下市股票的歷史列（決定存活者偏差程度） | **DB 未啟動，無法查證** |
| F-03 | 事實待補 | `daily_market_data` 是否含上櫃（TPEx）資料（影響股票池規模 ~1000 vs ~1900） | **DB 未啟動，無法查證** |

---

## 4. 被拒絕方案

（尚無）

---

## 5. 假設

| # | 假設 | 依據 |
|---|---|---|
| A1 | 本週 MVP 僅處理**日頻**資料 | `daily_market_data` 為唯一行情表，repo 內無任何分鐘／tick 資料 |
| A2 | **不修改** `daily_market_data` schema，不新增任何 DB 表 | D-02 |
| A3 | 研究 pipeline 為**獨立流程**，不與 `jobs/run_daily.py` 每日選股整合 | D-01（CLI 入口）＋ D-02（只寫 artifacts） |
| A4 | **不新增任何 Python 依賴**。不使用 `pyarrow`／`scipy`（環境中有，但未列入 `requirements.runtime.txt`，而 `test/test_environment_pins.py` 會檢查釘選） | D-02 |

---

## 6. 風險

| # | 風險 | 影響 |
|---|---|---|
| R1 | 無 `amount` 欄位 → `amihud_20d` 只能以 `close × volume` 近似，`vwap_gap` 無真實 VWAP | 兩個候選因子需重新定義或刪除 |
| R2 | 無還原股價 → 除權息當日產生假跳空，動量／反轉／已實現波動全部受污染 | 因子值可信度；需在文件明確揭露 |
| R3 | 無上市／下市狀態表 → 存活者偏差（survivorship bias）無法消除 | 未來 Rank IC 結果會系統性偏樂觀 |
| R4 | 現有 `calculate_natr` / `calculate_cross_sectional_zscore` 以 0 填補缺值 | 直接重用會污染研究結果，需決定「重用 vs 新寫」 |
| R5 | 無 CI、無 lint／type-check | 「阻擋條件」只能靠本地指令，需明確定義 |
| R6 | **repo 內存在兩套互相矛盾的股票池定義**：`db_helper.filter_common_stock_universe`（4 位數字、排除 03/08 → 保留 `0050`）vs `strategies/base.py:253` 的 `_exclude_warrants`（明確保留 ETF＋債券 ETF） | D1 的 universe 介面必須擇一或明確第三定義，否則研究與線上選股不一致 |
| R8 | **B2 的 baseline 不可重現**：`test/test_push_to_line_flex.py::test_run_evening_broadcasts_uniform_carousel` 在 MySQL 未啟動時失敗（`core/db_helper.py:819` `ConnectionError`），MySQL 啟動時則未知。既有測試套件的通過與否**取決於環境而非程式碼** | D-14 的 B2「不得新增失敗」需要一份可重現的 baseline，但目前無法產生。D1 開工前必須先在**固定環境條件下**凍結 baseline 清單，並在文件中註明該條件（DB 是否啟動） |
| R7 | **repo 內完全沒有除權息／減資／分割資料**（grep `除權｜除息｜dividend｜split｜ex_date` 僅命中策略命名，非資料） | 還原股價無法由現有資料推導；唯一來源是 `yfinance==0.2.66`（已釘選），但那是一條新的 ingestion pipeline |

---

## 7. 延後工作（明確非目標）

| 項目 | 延後至 | 理由／觸發條件 |
|---|---|---|
| 未來報酬（forward return）、Rank IC、分位數分析 | D3+ | 本週僅到因子值產出 |
| `rank_cs`、`rank_ts` 算子 | D3 | D-13：12 個因子皆未使用 |
| 多因子策略、事件回測 | D4+ | — |
| Web／LINE 前端回饋 | 未定 | D-01：本週交付契約，非 route 或訊息模板 |
| `factor_values` DB 表 | 首個因子通過 Rank IC 後 | D-02 |
| 還原股價（adjusted price）pipeline | 因子進入上線決策前 | D-03 revisit trigger |
| 修正 `filter_common_stock_universe` 的 ETF 過濾 bug | 未定 | D-05；記入 `docs/refactor/remaining_tech_debt.md` |
| 上市／下市狀態表（消除存活者偏差） | 未定 | D-06 C4 |
| 處置股／全額交割股資料 | 未定 | D-07 C5 |
| CI、lint、type check、覆蓋率門檻 | 未定 | D-14：本週不建 |
| 修改既有回測的成交時點 | **永不**（刻意分歧） | D-04 |

---

## 8. 強制條件彙總（C1～C7）

| # | 條件 | 來源 |
|---|---|---|
| C1 | 因子 metadata 含 `price_basis = "raw_unadjusted"`，寫在資料／registry 中 | D-03 |
| C2 | 驗證器含 `suspicious_gap` 檢查（單日 \|close/prev_close − 1\| > 20%） | D-03 |
| C3 | `docs/` 載明：還原股價到位前，Rank IC 為**下限**估計，不得用於上線決策 | D-03 |
| C4 | universe 函數輸出**每日檔數序列** artifact；若單調遞增則載明「存活者偏差未消除，Rank IC 為**上限**估計」 | D-06 |
| C5 | `docs/` 載明流動性以 `close × volume` 近似、處置股分盤未處理 | D-07 |
| C6 | pivot 成寬表時**必須斷言 `(trade_date, stock_id)` 唯一**，禁用 `pivot_table` 靜默聚合 | D-08 |
| C7 | WARN 必須輸出為 `artifacts/factors/validation_report.csv`，不得僅 print | D-15 |

---

## 9. 阻擋條件彙總（B1～B4）

| # | 條件 | 來源 |
|---|---|---|
| B1 | `pytest test/unit/research/ -q` 100% 通過 | D-14 |
| B2 | `pytest test/ -q` 不得**新增**失敗（比對凍結 baseline，不要求全綠） | D-14；⚠️ 見 R8 |
| B3 | 因子輸出檔中無 Inf；NaN 比例被記錄 | D-14 |
| B4 | look-ahead sentinel 測試通過（末 5 天改極端值，斷言倒數第 6 天以前不變，`rtol=1e-9`／`atol=1e-12`） | D-14 |
