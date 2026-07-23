# Stock_Linbotv1 專案目標

> 生效日期：2026-07-22
> 適用範圍：系統架構、量化策略、回測引擎、資料流程、Web、LINE Bot、排程工作與 OpenSpec 變更

## 1. 專案使命

Stock_Linbotv1 的目標是建立一套面向台股的自動化量化研究、選股、回測、推薦落庫與訊息推播系統。

系統必須同時具備：

1. **可行性 Feasibility**

   * 能在現有 Python、Flask、MySQL、LINE Bot、Plotly、XGBoost 與 MCP 技術棧上持續運作。
   * 優先改善既有模組，而不是無必要地全面重寫。
   * 所有架構設計必須考量現有部署、資料庫、CLI、Web 與排程相容性。

2. **可靠性 Reliability**

   * 相同資料、設定與交易規則必須產生可重現的結果。
   * 推薦結果必須以已落庫 snapshot 為主要資料來源。
   * 資料缺漏、零候選、外部服務失敗及指標無法計算時，必須具有明確狀態，不得靜默失敗。
   * 回測不得使用未來資料，財務資料必須依實際公告日期生效。

3. **可維護性 Maintainability**

   * 程式碼應具備清楚責任、穩定介面、合理封裝及完整測試。
   * 優先降低重複程式、巨型函式、巨型類別、隱性依賴與跨層耦合。
   * 每次重構必須小步執行，保留相容入口並提供回復方式。

4. **可驗證性 Verifiability**

   * 所有策略、風險指標、回測結果與資料契約都必須可測試。
   * 結構重構前應先建立 characterization tests。
   * 策略績效必須區分樣本內與樣本外結果。
   * 回測穩定性必須可透過 Walk-forward、Bootstrap、參數敏感度及交易成本分析驗證。

5. **可理解性 Readability**

   * 類別、函式、變數及策略名稱必須反映真實用途。
   * 禁止使用無法表達策略本質的名稱，例如 `turbo`、`innovation` 或純版本代碼作為主要名稱。
   * 舊版代碼可保留為 alias，但新程式必須使用語意化名稱。

## 2. 當前優化目標

本階段優化以「先穩定系統，再改善策略」為原則。

### 2.1 專案結構

* 盤點並移除可證明無引用的死碼、重複程式與產物檔案。
* 保留 `app/`、`jobs/`、`core/`、`services/` 為現階段 canonical 架構。
* 新功能不得繼續加入 legacy facade。
* Legacy 入口僅負責轉呼叫 canonical 模組，不得包含業務邏輯。
* 避免為了形式上的分層建立大量只有數行內容的檔案。

### 2.2 回測系統

* 將回測流程、投資組合、成交規則、交易成本、績效計算及視覺化解耦。
* 建立統一的 `BacktestResult`。
* 單策略與多策略組合必須共用同一套績效指標實作。
* 視覺化不得重新執行策略或回測。

### 2.3 策略系統

* 所有策略繼承穩定的 `BaseStrategy` 抽象。
* 策略透過多型提供統一的選股、排序與出場介面。
* 策略參數必須封裝於設定物件，不得散落於多個模組。
* 策略名稱必須反映實際因子與市場假設。
* 舊策略代碼保留為相容 alias。

### 2.4 風險評估

新增並統一計算：

* CAGR
* Annualized Volatility
* Sharpe Ratio
* Sortino Ratio
* Calmar Ratio
* Maximum Drawdown
* Downside Deviation
* Drawdown Duration
* Recovery Duration
* Profit Factor
* Maximum Consecutive Losses
* Exposure
* Turnover

第一階段僅用於報表與分析，不直接改變交易訊號或配置權重。

### 2.5 穩定性驗證

新增：

* IS/OOS 切分
* Walk-forward analysis
* Rolling Sharpe、Sortino、MDD
* Bootstrap 分布及信賴區間
* 參數熱力圖
* 交易成本敏感度
* 策略報酬相關矩陣
* 持股重疊率
* 有無市場風控對照

## 3. 成功標準

本階段完成時應符合：

1. 既有 CLI、Web、LINE Bot 與排程仍可使用。
2. 重構前後相同條件下的交易序列一致。
3. 所有刪除檔案皆附有無引用證據。
4. 每個策略都有語意化名稱、穩定識別碼與 legacy alias。
5. 風險指標具有統一定義、邊界條件及單元測試。
6. 回測結果可以直接產生穩定性報告。
7. 核心業務邏輯不依賴 Flask、Plotly 或 LINE SDK。
8. 新增模組必須降低複雜度，而非只將一個大型檔案拆成多個互相耦合的小型檔案。

# Stock_Linbotv1 開發憲法

> 版本：1.0
> 生效日期：2026-07-22
> 本文件優先於單次 Agent 對話中的臨時架構偏好。

## 第一條：可運作優先

所有設計必須先證明可在目前技術棧與部署環境中運作。

不得僅為追求理想架構而：

* 全面重寫已穩定運作的模組。
* 破壞現有 CLI、Web、LINE Bot 或排程。
* 引入無明確效益的新框架。
* 增加超出專案規模所需的基礎設施。
* 將簡單問題抽象成多層工廠、容器或事件系統。

採用新設計前，必須說明：

1. 解決的具體問題。
2. 對既有流程的影響。
3. 遷移方式。
4. 測試方式。
5. 回復方式。

## 第二條：可靠性優先於功能數量

新增功能前，必須確保現有資料與結果契約可靠。

系統必須遵守：

* 使用者可見推薦優先讀取已落庫 snapshot。
* heartbeat 表示策略當日已完成但沒有候選，不得被視為資料缺漏。
* 缺口檢查以「交易日 × 策略」為單位。
* 相同策略只能回退至自身歷史結果。
* 外部資料服務失敗必須回傳明確錯誤或退化狀態。
* 批次工作應盡量具備冪等性。
* 重跑相同日期不得無限制產生重複資料。
* 回測與模型訓練必須可設定 random seed。
* 不得以 `except Exception: pass` 隱藏錯誤。

## 第三條：單一真實來源

每一類設定或資料契約只能有一個 canonical source。

目前主要單一來源包括：

* 資料庫操作：`core/db_helper.py`
* 策略註冊：`StrategyManager.STRATEGY_REGISTRY`
* 策略啟用及持久化：`strategy_settings.json`
* 外部市場資料存取：`core/mcp_client.py`
* MCP 服務：`services/mcp/server.py`
* Web：`app/web_server.py`
* LINE：`app/line_bot.py`
* 批次工作：`jobs/`
* OpenSpec 變更：`openspec/changes/`

Legacy facade 只能轉送呼叫，不得複製核心邏輯。

## 第四條：封裝 Encapsulation

物件必須隱藏內部狀態與實作細節，只公開穩定且必要的操作。

要求：

* 投資組合的現金、持股與損益不可由外部任意修改。
* 交易成本由成本模型負責，不得散落在回測流程中。
* 策略參數應封裝於設定類別或不可變資料物件。
* 資料庫查詢細節不得洩漏至 Web、LINE 或策略類別。
* XGBoost 模型載入、預測與特徵順序應由專責物件封裝。
* 類別欄位預設為內部狀態，僅在有明確需求時公開。

優先提供具語意的方法：

```python
portfolio.open_position(order)
portfolio.close_position(order)
strategy.generate_candidates(context)
metrics.calculate(equity_curve, trades)
```

避免外部直接操作：

```python
portfolio.cash -= amount
portfolio.positions[stock_id] = raw_dict
```

## 第五條：多型 Polymorphism

相同性質的元件應透過共同介面替換，不應在核心流程中大量判斷具體類型。

策略應遵守共同介面，例如：

```python
class BaseStrategy(ABC):
    @abstractmethod
    def generate_candidates(self, context):
        ...

    @abstractmethod
    def rank_candidates(self, candidates, context):
        ...

    def should_exit(self, position, context):
        ...
```

回測引擎只依賴 `BaseStrategy`，不得出現：

```python
if strategy_name == "v31":
    ...
elif strategy_name == "v34":
    ...
```

適合使用多型的元件包括：

* 選股策略
* 排名模型
* 交易成本模型
* 滑價模型
* 部位配置模型
* 資料供應器
* 報告輸出器

## 第六條：繼承 Inheritance

繼承只用於穩定且真正具有「is-a」關係的抽象。

允許的例子：

* `HybridTrendRankStrategy` 是 `BaseStrategy`
* `FixedSlippageModel` 是 `SlippageModel`
* `HtmlReportRenderer` 是 `ReportRenderer`

禁止：

* 僅為重用兩三個工具函式而建立父類別。
* 超過兩至三層的深層繼承。
* 父類別掌握大量子類別專屬條件。
* 透過繼承共享可變狀態。
* 建立抽象類別但只有一個實作，且沒有明確替換需求。

原則：

> 穩定介面使用繼承；行為組合優先使用 composition。

## 第七條：組合優於繼承

策略所需能力應優先由可組合元件提供，例如：

```python
strategy = GrowthMomentumStrategy(
    factor_model=factor_model,
    risk_filter=risk_filter,
    ranking_model=ranking_model,
    exit_policy=exit_policy,
)
```

不得建立：

```text
BaseStrategy
  └─ MomentumStrategy
       └─ GrowthMomentumStrategy
            └─ GrowthMomentumWithBreadthStrategy
                 └─ GrowthMomentumWithBreadthAndMFI...
```

當新增一個條件就需要新增一層子類別時，代表設計應改為組合。

## 第八條：介面隔離

介面必須小而明確。

不得要求所有策略實作與其無關的方法。

可將介面拆為：

* `CandidateGenerator`
* `CandidateRanker`
* `ExitPolicy`
* `PositionSizer`
* `RiskFilter`

但只有在存在兩個以上實作或明確替換需求時才建立獨立抽象。

禁止為每一個函式建立一個檔案或介面。

## 第九條：依賴反轉

核心領域不得依賴外部框架細節。

依賴方向應為：

```text
Web / LINE / Jobs
        ↓
Application Services
        ↓
Domain / Strategy / Backtest
        ↓
Interfaces
        ↑
Database / MCP / Plotly implementations
```

要求：

* 策略不得 import Flask。
* 回測引擎不得 import Plotly。
* 績效模組不得依賴策略類別。
* Web route 不得直接實作 SQL。
* LINE message builder 不得負責重新計算推薦。
* 核心業務物件不得直接讀取環境變數。

## 第十條：避免過度拆檔

模組化的目的在降低認知負擔，不是增加檔案數量。

建立新檔案前必須符合至少一項：

1. 具有獨立且清楚的業務責任。
2. 可被兩個以上模組重用。
3. 需要獨立測試或替換。
4. 原檔案已因多項責任而難以理解。
5. 新模組能明顯降低循環依賴。

以下情況不應拆檔：

* 新檔案只有一個三至五行函式。
* 新檔案只被單一位置使用，且沒有獨立概念。
* 拆分後必須跨三個以上檔案才能理解一個簡單流程。
* 新增大量 `manager`、`helper`、`utils`，但責任不明。
* 只是為了讓每個檔案行數變少。

建議限制：

* 一個檔案主要負責一個內聚領域，不要求一個檔案只放一個類別。
* 一個類別應有一個主要變更原因。
* 一個函式原則上控制在可一次閱讀理解的範圍。
* 超過約 300 至 500 行的核心模組應檢查是否存在多重責任，但不得機械式拆分。
* 共用工具必須依領域命名，避免建立無邊界的 `utils.py`。

## 第十一條：可讀性

程式碼應優先表達意圖。

要求：

* 名稱應使用完整英文語意。
* 避免不明縮寫與魔術數字。
* 策略參數必須有名稱、型別、預設值及說明。
* 複雜判斷應拆成具業務語意的方法。
* 註解應解釋「為什麼」，不是重述程式碼。
* 公開類別及公開方法應提供簡潔 docstring。
* 同一概念不得同時使用多種名稱。
* Boolean 名稱使用 `is_`、`has_`、`should_` 或 `can_` 前綴。
* 日期、報酬率與百分比的單位必須明確。

## 第十二條：策略設計規範

每個策略必須包含：

* 穩定策略 ID
* 中文顯示名稱
* 英文顯示名稱
* Legacy alias
* 市場假設
* 選股因子
* 排序因子
* 出場規則
* 適用行情
* 失效條件
* 必要資料欄位
* 預設參數
* 回測基準

策略名稱不得宣稱程式中不存在的條件。

例如：

* 沒有股利殖利率條件，不得稱為高殖利率策略。
* 沒有趨勢過濾，不得稱為趨勢過濾均值回歸。
* 沒有價值因子，不得稱為價值策略。

## 第十三條：回測正確性

回測必須明確定義：

* 訊號產生時間
* 成交時間
* 成交價格
* 滑價
* 手續費
* 證交稅
* 最低手續費
* 資金不足處理
* 部位限制
* 停損停利觸發順序
* 同日多訊號的排序
* 財報及月營收生效日期

禁止：

* 使用當日收盤資訊並假設能以同一收盤價成交，除非規格明確允許。
* 使用財報期間結束日代替公告日。
* 在 OOS 結果出現後回頭調整同一組參數，卻仍稱為 OOS。
* 隱藏交易成本。
* 因結果不佳而任意排除交易。

## 第十四條：風險指標規範

風險指標必須由統一 metrics 模組計算。

所有指標都要明確定義：

* 使用的報酬頻率
* 年化係數
* 無風險利率
* 最低可接受報酬
* 缺失值處理
* 樣本數門檻
* 無限值處理

當指標不可計算時：

* 回傳 `None` 或結構化不可用狀態。
* 附上不可計算原因。
* 不得用零偽裝有效結果。
* 不得默默回傳 infinity。

## 第十五條：測試憲法

每次結構重構至少應具備：

1. Characterization test
2. Unit test
3. Integration test
4. Regression test

高風險流程必須覆蓋：

* 推薦 fallback
* heartbeat 語意
* 策略持久化
* Web、LINE、scheduled push 一致性
* 回測交易序列
* 交易成本
* 日期與資料可用性
* IS/OOS 切分
* Walk-forward 無洩漏

測試失敗時不得：

* 直接刪除測試。
* 放寬 assertion 以掩蓋錯誤。
* 將真實差異全部改成浮點誤差。
* 更新 baseline 而不說明行為差異。

## 第十六條：刪除與死碼清理

刪除檔案前必須檢查：

* Import
* CLI
* 排程
* Docker
* GitHub Actions
* 文件
* 測試
* Web route
* LINE Bot
* Agent 設定
* OpenSpec
* 字串形式動態載入

刪除項目必須記錄：

* 路徑
* 無引用證據
* 刪除原因
* 影響
* 回復方式

無法證明無用時，標記為 `UNKNOWN` 或 `DEPRECATED`，不得刪除。

## 第十七條：OpenSpec 日期規範

所有 OpenSpec Change 名稱必須使用建立日期作為前綴。

格式：

```text
YYYY-MM-DD-<change-name>
```

範例：

```text
2026-07-22-refactor-quant-system-foundation
2026-07-22-cleanup-dead-code
2026-07-22-modularize-backtest-engine
2026-07-22-add-risk-adjusted-performance
2026-07-22-add-backtest-stability-analysis
```

執行命令：

```text
/opsx:propose 2026-07-22-refactor-quant-system-foundation
```

Change 目錄：

```text
openspec/changes/2026-07-22-refactor-quant-system-foundation/
```

每個 `tasks.md` 任務標題也必須使用日期前綴：

```markdown
## [2026-07-22] Phase 1：Repository inventory

- [ ] [2026-07-22] 1.1 建立 repository inventory
- [ ] [2026-07-22] 1.2 建立 file reference map
- [ ] [2026-07-23] 1.3 確認 deletion candidates
```

日期定義：

* Change 前綴使用提案建立日期。
* Task 前綴使用預計開始日期。
* 完成後不得修改原開始日期。
* 延期時保留原日期，另加 `rescheduled` 註記。
* 文件內所有日期使用 ISO 8601：`YYYY-MM-DD`。
* 不使用「今天」、「明天」、「下週」等相對日期。

## 第十八條：OpenSpec 任務粒度

每個任務必須能獨立驗證，並包含：

* 開發日期
* 目標
* 修改範圍
* 明確非目標
* 預期修改檔案
* 驗收條件
* 測試命令
* 風險
* 回復方式

任務不得同時混合：

* 結構重構與策略條件調整。
* 資料庫遷移與 UI 改版。
* 死碼刪除與新功能開發。
* 風險指標新增與動態資金配置。
* 回測引擎拆分與績效最佳化。

## 第十九條：架構決策

重大架構決策應在 OpenSpec `design.md` 中記錄：

* Context
* Decision
* Alternatives
* Consequences
* Migration
* Rollback

以下變更必須記錄設計決策：

* 新增核心抽象類別。
* 改變 canonical 入口。
* 改變資料庫存取方式。
* 改變策略介面。
* 改變成交規則。
* 改變推薦 fallback contract。
* 引入新框架或服務。
* 建立新的跨模組依賴。

## 第二十條：完成定義

OpenSpec 任務只有在以下條件全部成立時才能標記完成：

* 程式實作完成。
* 新增或更新測試。
* 測試通過。
* Lint 與型別檢查通過，或已記錄既有例外。
* 文件已更新。
* 相容性已驗證。
* 行為差異已記錄。
* 回復方式已驗證。
* `tasks.md` 已標記實際完成日期。

完成格式：

```markdown
- [x] [2026-07-22] 1.1 建立 repository inventory
  - Completed: 2026-07-22
  - Verification: `python -m pytest test/ -q`
  - Evidence: `docs/refactor/repository_inventory.md`
```
