# 2026-10-19 TQuant 策略變更驗證

Change：`2026-10-19-add-tquant-strategies`；schema：`spec-driven`。
依 `openspec-verify-change` 核對 proposal、design、兩份 delta specs 與 tasks，無省略的驗證維度。

| 維度 | 結果 |
|---|---|
| Completeness | 18/18 任務完成，4/4 requirements 已實作 |
| Correctness | 10/10 scenarios 有實作與測試或實測證據 |
| Coherence | 符合更新後的來源欄位稽核、BaseStrategy 與既有執行流程設計 |

CRITICAL：0。WARNING：0。SUGGESTION：0。
All checks passed. Ready for archive. 本次未執行封存。

## 最終範圍

41 份 notebook 中實際上架 12 支技術策略：MACD、KD、MAD、乖離率、布林、
迴歸動量、量能、阿隆、趨勢跟蹤、逆勢回檔、長期趨勢、VAM。
策略中心共 20 筆：原 7 支、新 12 支、基本面 shadow 1 支。
LINE 選單保持原 7 支；Web、Daily、預覽與 CLI `all` 涵蓋已登錄的新策略。

新增排除 18 支候選，加上原定排除的 11 份，共 29 份不納入。
延伸量能缺歷史台灣 50 成分資格，德伍卻斯缺市值與負債率等基本面；
籌碼、月營收、Alpha 與 12 份大師/品質候選均未通過完整欄位或執行模型稽核。
逐支必要欄位、缺項與分類已寫入本地 change 的 `design.md`，沒有替代欄位或刪條件上架。
因此這次沒有新增籌碼、基本面或品質策略。

## 規格與情境對照

| Requirement / Scenario | 實作證據 | 驗證證據 |
|---|---|---|
| Canonical-only listing：清單等於 canonical metadata | `core/strategy_manager.py:86`、`:517` | `test/test_tquant_strategies.py:45`；CLI `all` 展開比對 |
| Public surfaces 不重複 alias | 同一 CANONICAL_REGISTRY；`app/web_server.py:110` | registry、catalog API 測試，Chromium 實測 |
| LINE picker 保留 7 支 native | `app/line_flows.py:174` | `test/test_tquant_strategies.py:45` |
| Complete source data：缺現金流不註冊 | schema inspection 與 design 的 41 份對照表 | `outputs/tquant_change/schema.json`、`notebook_hashes.json`；測試精確限定 12 個 ported IDs |
| Original strategies unchanged | 原七支策略、回測核心、成本與滑價沒有修改 | 真實 CLI 前後 70 筆 BUY/SELL 比對，另保留原選股 fixtures |
| Truthful catalog：匿名載入所有策略 | `app/web_server.py:110`、`:214` | `test/test_tquant_strategies.py:75`；Chromium 20 筆目錄 |
| 技術篩選含 MACD、不含 quality | `templates/strategy_center.html` 的既有 category filter | Chromium 點選技術與品質按鈕 |
| 基本面篩選與 runtime 同列 | category mapping `app/web_server.py:119` | Chromium 基本面篩選；本次無符合欄位閘門的新增基本面策略 |
| 手機來源與能力原因分開顯示 | `templates/strategy_center.html:124` | Chromium 390px、來源與差異說明、無水平溢出及 JS 例外 |
| API additive enrichment、保留原身份 | `app/web_server.py:148`；原 aliases 不改 | catalog API 相容測試、原七支 identity/alias tests |

## 實際驗證結果

- 基線：`python -m pytest test -q` → **1057 passed, 1 skipped, 4 xfailed**。
- 完成後同一完整指令 → **1074 passed, 1 skipped, 4 xfailed**，266.88 秒；新增 17 個測試通過。
  skip/xfail 數量與基線相同。既有 warnings 數量同為 2722。
- `python scripts/verify_tquant_cli.py`：12 支均經真正的 `4_run_backtest.py` 相容入口完成。
  使用 2026-10-16 單日行情與 `--no-persist`，無篩選例外；多支有實際買單。
  MACD 另完成 2026-10-01 至 2026-10-16 的 CLI 驗收。
- `python scripts/verify_tquant_change.py before/after`：2026-09-01 至 2026-10-16、
  seed `2026-10-19`，原七支共 **70 筆**交易的日期、代碼、方向、順序與數量完全一致；
  成交價使用 `rtol=1e-9`、`atol=1e-12` 通過。
  原七支每日選股數量亦與基線完全一致。
- 每日 dry-run：**7 支 31.55 秒 → 19 支 65.69 秒**，約 2.08 倍，增加 34.14 秒。
  19 支皆成功，`strategy_errors={}`；157 筆預覽推薦列/零候選心跳。
- `python scripts/verify_tquant_browser.py`：1440px、390px 的分類、預覽、每日套用確認視窗、
  回測 GET 導向與手機來源說明通過。Daily POST 另使用隔離 settings 測試成功。
- `git diff --check` 通過；`openspec validate 2026-10-19-add-tquant-strategies --strict` 通過。
- `strategy_settings.json` 保留使用者原有修改，驗證程式對每次執行前後的 bytes 進行不變斷言；
  圖片刪除與未追蹤的 `Strategy_example/` 也予以保留。

## 設計與限制

沿用既有 registry、BaseStrategy、StrategyRunner、Daily 與回測引擎，未新增依賴或設定介面。
共用 `_tquant.py` 只負責 as-of 行情及完整窗格；不修改原七支策略的資料路徑。
MACD 保留原 notebook 的 2330 與 35 日 SMA 起始 EMA；引擎價格上限仍適用。
VAM 的母體依提案改為本地個股全市場；部位縮放、持股數、再平衡與出場採本專案引擎。
每張新卡片均揭露來源與績效差異。

耗時是單次 dry-run，未包含新聞外部服務與正式落庫，並非正式排程耗時保證。
本次不修改 `persistence_strategies` 或 active 設定；新策略持久化預覽快照於下一次正式每日執行後產生。
CLI 驗收只證明執行能力，沒有宣稱新策略績效優於原 notebook。

OpenSpec 文件按現有測試契約僅留本地，不納入 Git。
目前 CLI 的 status/instructions 拒絕日期開頭名稱，已透過暫存 junction 讀取同一變更；
正式目錄名稱維持 `2026-10-19-add-tquant-strategies`，strict validation 直接驗證此名稱。

## Ponytail review

Lean already. Ship.

## 提交與證據

- `7fabb70`：原七支真實交易基線與來源稽核。
- `1406687`：MACD 試點、目錄/LINE、來源與瀏覽器驗收。
- `2b09853`：其餘通過稽核的技術策略、fixture 與 CLI 證據。
- `4db23c1`：維持 OpenSpec 本地政策與延伸公開清單測試契約。

機器可讀證據位於 `outputs/tquant_change/`：`before.json`、`after.json`、`schema.json`、
`notebook_hashes.json`、`cli.json`、`verification.json`、`mobile.png`，並保存完整測試與 CLI 日誌。
