# Design: TQuant 策略併入策略中心

## 決策 1：改寫選股條件，不引入 zipline / TEJ

| 方案 | 結論 |
|------|------|
| A. 安裝 zipline-tej，原樣執行 notebook | 否決。需要付費 TEJ API key、建立第二套回測引擎與 bundle 資料，違反「CLI、Web、回測單一路徑」的原則。 |
| B. 新增「定期再平衡、目標權重」回測模式 | 否決。這會修改回測核心，依 AGENTS.md 需要另立 characterization baseline，超出本次需求。之後若需要，另開 change 處理。 |
| **C. 把 notebook 的選股條件改寫成 `BaseStrategy.filter_candidates()`** | **採用**。沿用現有引擎、成本、滑價與 UI，改動最小。 |

方案 C 的已知限制必須寫在每張卡片的 description 中：持股數、再平衡頻率與出場規則採用本專案引擎（最多 3 檔、BULL 進場、移動停損），因此績效**不會**與 notebook 一致。notebook 只作為邏輯來源，不作為績效對照基準。

## 決策 2：與現有策略同列，依既有分類歸類

- 新策略直接登錄進 `CANONICAL_REGISTRY` 與 `STRATEGY_METADATA`，沒有獨立的 registry 或生命週期。id 採 snake_case，例如 `macd_cross`。`legacy_ids=()`，必須與既有 id 及 alias 無衝突，由 `StrategyRegistry` 的重複檢查保證。
- 分類對應既有策略中心篩選（`app/web_server.py` 的 `category_labels`）：

| `category` | 篩選 | 適用 |
|------------|------|------|
| `trend` / `reversion`（既有，落入預設的 Technical） | 技術 | P2 技術面策略 |
| `flow`（既有） | 籌碼 | 跟隨大戶、融資維持率 |
| `fundamental`（**新增**，對應 `Fundamental`） | 基本面 | 月營收、尋找 Alpha（PER）、大師選股類 |
| `quality` / `value`（既有） | 品質 | 以品質因子為主的策略（例如 IMQ），稽核時逐支決定 |

- `StrategyMetadata` 新增 `source: str = "native"`，新策略設為 `"tquant"`。它只用在兩處：卡片顯示來源 notebook 與模型差異說明，以及 LINE 選單篩選。
- 能力：Daily、回測與預覽都與既有策略相同。`get_persistence_strategy_names()` 在 `persistence_strategies` 未設定時預設涵蓋整個 `STRATEGY_REGISTRY`，所以 `run_daily` 會自動為新策略產生快照，不需要改程式。
- LINE：`_list_strategy_picker_options()`（`app/line_flows.py:174`）只列出 `source == "native"`，避免超過 Flex 選單 13 項的上限而被截斷。

## 營運風險

- `run_daily` 的執行時間會隨策略數線性增加。實作時需實測前後耗時。若超過排程容許時間，再由使用者自行決定是否設定 `persistence_strategies`（本 change 不寫入 settings）。
- 新策略若被加入 `active_strategies` 或 `random_strategy_pool`，就會進入 LINE 每日推播。這是使用者既有的設定行為，本 change 不主動加入。

## 決策 3：資料稽核閘門

每支策略移植前，先列出 notebook 實際用到的欄位，並對照本地欄位。**任一必要欄位缺少就不納入**，原因記在下表。不得用其他欄位代替，也不得刪掉條件後照常上架。

### Notebook 對照表（共 41 份）

| 批次 | Notebook | 預期本地資料 | 狀態 |
|------|----------|--------------|------|
| P2 技術 | TQ_KD指標回測實戰 | daily_market_data（自算 KD） | 待移植 |
| P2 技術 | TQ_MACD交易策略 | daily_market_data `macd*` | 待移植 |
| P2 技術 | TQ_MAD_移動均線距離策略 | 收盤價、均線 | 待移植 |
| P2 技術 | TQ_乖離率策略 | 收盤價、均線 | 待移植 |
| P2 技術 | TQ_布林通道交易策略 | `bb_*` | 待移植 |
| P2 技術 | TQ_動量策略 | 收盤價報酬 | 待移植 |
| P2 技術 | TQ_延伸量能回測策略 | `Component_Stock_of_TWN50_Fg`、20 日對數價格迴歸動量 | 不納入：缺歷史台灣 50 成分資格；不能改用全市場 |
| P2 技術 | TQ_量能回測實戰 | 成交量 | 待移植 |
| P2 技術 | TQ_阿隆指標交易策略 | 高低價（自算 Aroon） | 待移植 |
| P2 技術 | TQ_趨勢跟蹤策略 | 收盤價、均線 | 待移植 |
| P2 技術 | TQ_逆勢交易策略 | 收盤價、RSI | 待移植 |
| P2 技術 | TQ_長期趨勢策略 | 收盤價、長均線 | 待移植 |
| P2→基本面 | TQ_德伍．卻斯動能策略 | 市值、5 年 EPS CAGR、TTM 每股淨利、負債率、PER、6 月相對強度 | 不納入：缺市值、負債率、TTM 每股淨利與完整 5 年 EPS；原提案誤列純技術 |
| P2 技術 | TQ_VAM_波動率調整截面動能_TWN100 | 報酬、波動率（宇宙改為本地全市場，需在 description 註明） | 待移植 |
| P3 籌碼 | TQ_跟隨大戶策略 | 大戶持股張數變動、持股比例及 5 日均值（`total_vol`、`total_pct`） | 不納入：沒有大戶持股資料，法人淨買超不是持股比例 |
| P3 籌碼 | TQ_融資維持率 | 開收盤、實際成交金額及 10 日均值、融資餘額及 5 日均值 | 不納入：缺實際成交金額；另缺融資維持率。原碼最終訊號沒有使用維持率條件，仍不得以收盤×成交量代替成交金額 |
| P3 基本面 | TQ_尋找Alpha | 上市/櫃市場歷史分類、TEJ ROI、IR0001 報酬迴歸 alpha、PER 排名 z-score | 不納入：缺歷史市場分類與 TEJ ROI，不能刪掉迴歸只保留 PER |
| P3 基本面 | TQ_月營收成長率策略 | 月營收 YoY、年度毛利成長、營業利益成長、稅後淨利成長 | 不納入：缺稅後淨利及其年度成長；`revenue_yoy` 不能替代三個獲利條件 |
| P4 基本面 | TQ_David_Dreman_逆向投資策略 | 市值、PER、殖利率、流動資產/負債、總負債/權益、ROE、稅前淨利率、盈餘成長 | 不納入：缺市值、資產負債表、ROE、稅前淨利率與淨利成長 |
| P4 基本面 | TQ_James_OShaughnessy_Value_v1 | 市值、每股現金流、PCF、普通股股數、每股營收、PSR、殖利率 | 不納入：缺市值、現金流、股數、每股營收與 PSR |
| P4 基本面 | TQ_iValue | 市值、可交易資格、iB/M 因子（無形資本調整帳面價值） | 不納入：缺市值、權益及無形資本資料；PBR 不是 iB/M |
| P4 品質 | TQ_IMQ_IndMom_Quality | 歷史產業、市值權重產業動能、毛利/資產 GP_A、公告日期及版本化營收/財報 | 不納入：缺總資產、市值、歷史產業與公告版本；分類應為 quality |
| P4 基本面 | TQ_大師策略＿麥克．喜偉收益型選股法 | 產業、PER、流動比率、負債/權益、現金股利率、年度營收成長 | 不納入：缺產業、流動比率、負債與權益；現有殖利率不可假裝現金股利率 |
| P4 基本面 | TQ_安東尼·賣利亞反向操作策略 | 調整後價、外資/投信/董監持股率、四項基本面條件 | 不納入：缺調整係數、持股比例與完整基本面條件，不能只用 PBR |
| P4 基本面 | TQ_尤金·法瑪三因子模型策略 | 前年市值、PBR、調整後年報酬、上市普通股/一般產業資格 | 不納入：缺市值、調整係數與歷史資格 |
| P4 基本面 | TQ_從羅伯．加迪納的選股法出發 | 市值、淨利成長、TTM 毛利率、產業、董監持股率、PEG | 不納入：缺市值、淨利、產業、董監持股與 PEG |
| P4 基本面 | TQ_柏頓．墨基爾成功選股法則 | 產業（至少 40 家）、多年 TTM 營收/淨利成長、PER 歷史位置 | 不納入：缺產業、淨利及多年完整歷史 |
| P4 基本面 | TQ_查爾士．布蘭帝 價值型選股法則 | 總負債/權益、董監持股率、PER、PCF、PBR | 不納入：缺負債、權益、董監持股與現金流 |
| P4 基本面 | TQ_霍華羅斯曼_審慎致富投資法 | 市值、流動比率、4 季 ROE、5 年正自由現金流、營收/淨利成長 | 不納入：缺市值、流動比率、ROE、自由現金流與淨利 |
| P4 其他 | TQ_週期投資 | 景氣對策信號 `val_shifted`、0050 與債券/反向 ETF 權重切換 | 不納入：缺景氣信號，且是多資產配置而非個股選擇 |
| 不納入 | TQ-Future_Impluse_MACD / 三大法人籌碼面台指期 / 黃金交叉期貨、TQ_Future_波動率調整持倉動能期貨策略、TQ_波動率調整持倉動能期貨策略、TQ_期貨策略範例（6 份） | — | 引擎不支援期貨與放空 |
| 不納入 | TQ_ETF資產配置模型 | — | 需要多資產目標權重再平衡 |
| 不納入 | TQ_造市商與一般投資人在 ETF 折溢價套利… | — | 純分析，沒有交易策略 |
| 不納入（延後） | TQ_LTR_LambdaMART截面排名、TQ_機器學習算法 XGBoost… | — | 需要模型訓練管線與新依賴，另開 change |
| 不納入 | TQ_月營收成長率策略_simple_algo | — | 與「月營收成長率策略」邏輯重複 |

計數：P2 14 份 + P3 4 份 + P4 12 份 + 不納入 11 份 = 41 份。

## 檔案結構

```text
core/strategies/

    macd_cross.py          # 檔頭註明來源 notebook 檔名（與既有策略同層）
    ...
```

每個類別都要實作 `name`、`display_name`、`description`、`features`、`target_return`、`look_ahead_days` 與 `filter_candidates()`，並沿用預設出場邏輯。參數以類別常數寫死，數值取自 notebook，不新增設定介面（YAGNI）。

## 2026-10-19 實作稽核修正

實際資料庫為 MySQL，以 SQLAlchemy inspection 讀取正式 schema；證據存於
`outputs/tquant_change/schema.json`。`daily_market_data` 有 OHLCV、PER/PBR、法人買超與融資餘額；
`monthly_revenue` 僅有年月、營收、YoY；`financial_statements` 僅有營收、研發、EPS、毛利與營業利益相關欄位。
有欄位不代表有完整歷史，不以現有的零填值或最新財報回填歷史作為新策略的資料。

P2 實際可移植 12 支；延伸量能與德伍卻斯未通過閘門。P3 四支、P4 十二支均不納入，
因此本次沒有新增籌碼、基本面或品質策略；既有分類與基本面 shadow 策略繼續保留。
動量 notebook 的最後一個 `make_pipeline()` 覆蓋前一版本，實際只需價格與成交量：
使用全市場迴歸分數前 20 名與成交金額排名前 150 名的交集，不需要先前探索圖表中的市值/產業欄位。

`_tquant.py` 共用 BaseStrategy 介面與單日截面轉接，批次讀取截至訊號日的 550 日行情，
足夠涵蓋 252 個交易日；不足窗格不發訊號。只納入本地四碼個股及大盤交易日，
排除未來、停牌當日無資料、ETF 與重複列。快取僅保留最近一個 as-of date，
同日資料更新後可呼叫 `load_history.cache_clear()`；不影響原七支的計算路徑。
MACD 保留原 notebook 的 2330 單一標的、35 日窗及 TA-Lib SMA 起始 EMA；
本地引擎的價格範圍仍適用，故 2330 高於 500 時回測可能沒有交易。
趨勢跟蹤與長期趨勢只取多頭進場分支；VAM 依提案允許使用本地個股全市場，
並保留流動性前 100、VAM 前 10 的選擇。部位縮放、再平衡與出場一律由本專案引擎負責。

OpenSpec strict validation 可直接驗證日期名稱。此版本 `status/instructions` 拒絕數字開頭，
使用暫存工作區的 `add-tquant-strategies` junction 指向同一變更讀取工作流程，正式目錄不改名。
