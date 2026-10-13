# 2026-10-13 市場資料與 Web 複雜度檢視

## ponytail-audit

掃描 335 個 tracked Python 檔（173 個 app/core/jobs/services/scripts 模組）、頂層長函式引用、抽象基底／Protocol／adapter、CLI 相容代理、服務依賴清單與既有刪除證據。静態單次名稱出現不視為完整刪除證據。

依可移除規模排序，以下均已處理：

- delete: `jobs/update_database.py` 無呼叫的 TWSE／TPEx 直連抓取器、HEADERS、requests import。由既有 canonical MCP 路徑取代，證據在 deletion_candidates.md。
- delete: `services/mcp/server.py` 重複 ETF 請求與不可辨識 fallback。新版 TPEx dailyQuotes 已包含 ETF；include_etfs 在 canonical prepare 階段過濾。
- shrink: `services/mcp/server.py` 兩段市場合併收尾。共用 `_combine_market_frames` 並在同處拒絕單市場的 ALL 結果。
- shrink: `jobs/run_daily.py` 最新橫截面的第二次去重。將去重移到滾動計算前一次完成，避免重複歷史影响指標。

未作為刪除候選：

- `api_daily_signals`、`api_market_recommendations`、`api_strategies`、`backtest` 的低靜態引用來自 Flask decorator 路由，皆有公開入口及測試。
- `BaseStrategy` 有七個策略；manager、legacy adapters、root CLI 與舊 key aliases 有相容規格與既有呼叫。
- Fundamental identity/runtime Protocol 與 adapters 隔離 frozen/research/runtime 證據，有對應規格，不能只以類別數判定過度設計。
- requirements.txt 是開發／研究 frozen 環境；服務已有獨立 requirements.mcp.txt／requirements.runtime.txt。套件不存在完整跨 notebook、動態載入、CLI 無引用證據，保留依賴。

完成已證實的簡化後，沒有額外可安全刪除的候選。

net: -0 lines, -0 deps possible.

## ponytail-debt

- `scripts/ci_check_integration_marker_coverage.py:3`, 以全檔案 mitigating signal 判斷 DB 測試隔離。ceiling: 只攔完全沒有隔離訊號的測試，會低估。upgrade: 實際有未標記且未 mock 的 MySQL 測試漏過時擴充檢查。
- `jobs/update_database.py:394`, 成功空回應假日判斷。ceiling: 空回應可能是休市或未公布。upgrade: 可取得權威台灣交易日曆時用日曆核對。

2 markers, 0 with no trigger.

## ponytail-gain

以下是 skill 提供的五個任務、三個模型之 benchmark medians；不是本專案的成本或速度量測。

```text
ponytail gain                  benchmark median: 5 tasks, 3 models

Lines  no-skill  #################### 100%
       ponytail  ####................   6-20%  reduction 80-94%
Cost   no-skill  #################### 100%
       ponytail  ##########..........  23-53%  reduction 47-77%
Speed  ponytail  3-6x faster
```

本專案僅報告實際 git diff、掃描檔數與 2 個 debt markers；沒有推算不存在的開發基線。

## 2026-10-14 最終 ponytail-review

範圍為 `be8aeba..HEAD` 及本次未提交修改。重用原 MCP／crawler／SQL／Web 路徑，未新增依賴、factory 或 config。資料日期、NULL、歷史指標污染及正式排程／手動復原的回歸皆有具體失敗條件，不列為可刪除樣板。

Lean already. Ship.
