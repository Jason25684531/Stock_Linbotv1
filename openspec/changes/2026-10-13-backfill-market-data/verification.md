# 2026-10-13 補檔驗證證據

## Source phase

- 修改前相關基線 42 passed；新增來源契約後 48 passed。
- 驗證官方日期不符、ALL 單一市場、兩市均空、法人缺資料阻擋，以及 SQLite 實際保存 NULL／真實零值差異。
- ALL 完整性檢查共用同一個合併 helper，移除原本兩段重複收尾。
- 移除無呼叫的舊直連抓取器與本地 requests import／HEADERS；引用證據見 docs/refactor/deletion_candidates.md。
- git diff --check 通過；openspec validate 2026-10-13-backfill-market-data --strict --no-interactive 通過。
- OpenSpec 1.3.0 的 new/status/instructions 不支援日期開頭 ID。依專案日期目錄規則直接建立與讀取全部 artifacts；validate 支援該格式。

## Operational phase

- 第一次補檔被來源日期檢查阻擋，行情未寫入。TPEx 舊收盤端點請求 d=115/10/12，payload date=20261013；modern dailyQuotes 使用 date=2026/10/12 正確回 date=20261012。
- 新 dailyQuotes 已含 ETF，移除非 JSON／重複 ETF 請求；TPEx 24 欄法人表自營商合計改為 22（16 為自行買賣）。新增對應回歸後 50 passed。
- 新增 dealer_buy、margin_balance、short_balance BIGINT NULL DEFAULT NULL，保留舊欄位與歷史資料。補檔前月營收最新為 2026-09，財報最新為 2026Q2。
- 補入 10/12 2,308 筆、10/13 2,307 筆；各日 count=distinct(stock_id)，有效日期 2026-10-13。foreign_buy/dealer_buy NULL 分別 90／101；補足 TPEx margin 前的 margin_balance NULL 為 1,109／1,107。
- 10/09–10/11 原始列數仍為 19,791／13,194／6,597，沒有清除歷史列。
- Daily 的乾淨／污染歷史一致性測試修改前失敗（權證多出 1 檔），篩選無錨點日期、週末、權證、重複鍵後通過；daily characterization／CLI／persistence 15 passed，乾淨指標符合 rtol=1e-9、atol=1e-12。
- MOPS 2026-09 兩市場成功，新增／更新後月營收 1,842 筆（原 1,548）。
- 10/13 首次 Daily 成功，七策略均完成；V33／V37 heartbeat。因系統已到 10/14，關閉該次即時新聞，未用 10/14 新聞回寫 10/13。
- TPEx margin 表日期 20261012、有 919 列／20 欄；修正 tables 與券餘額欄位，新增源日期與欄位回歸，相關測試 30 passed。
- 更新兩日融資融券後有效餘額行數 2,000／2,001；NULL 剩餘 308／306。以 keyed UPDATE 只更新 margin_balance／short_balance，未覆寫已計算指標或價量。
- 複雜度 audit／debt／gain 詳見 docs/refactor/market-data-web-audit-2026-10-13.md；兩個 debt markers 均有 upgrade trigger，未移除相容面或套件。

## 2026-10-14 最終資料與 Web 實測

資料目標仍為 2026-10-13；沒有把 10/14 即時新聞當作歷史資訊。官方來源為 TWSE／TPEx 行情與法人、兩市融資融券、MOPS 月營收及季報，經既有 MCP／crawler 與資料庫提供給 Web。

| 日期 | 行情／不重複代碼 | 法人缺值 | 融資／融券缺值 | 不合理 OHLC |
| --- | ---: | ---: | ---: | ---: |
| 2026-10-12 | 2,308／2,308 | 90 | 308 | 0 |
| 2026-10-13 | 2,307／2,307 | 101 | 306 | 0 |

- 補檔後逐欄比對實際 MCP 官方資料與 DB 的 stock_id、OHLC、volume、foreign_buy、trust_buy、dealer_buy，兩日符合 rtol=1e-9、atol=1e-12，warnings 為空。
- 2026-09 月營收為 1,842 筆，最新季報 2026Q2 為 1,934 筆。無資料的個別欄位維持 NULL，不宣稱每個代碼均有來源記錄。
- 2026-10-13 實際推薦落庫共 43 筆候選、2 筆 NONE heartbeat：V31 3、V34/V35/V36/V38 各 10；V33/V37 無候選。七個 canonical API 的 market_anchor_date、recommendation_date 都是 10/13，has_persisted_snapshot=true、fallback_used=false；五策略 current、兩策略 heartbeat。
- 最後一次 Daily 的 manual/run_daily/2026-10-14 紀錄為 success，source_date=trade_date=2026-10-13、error_summary=NULL。既有 rows_inserted=1,138 計數為截斷前選股數，實際 persisted snapshot 數以上述 SQL 為準。PowerShell 將既有 pandas warning 當作 NativeCommandError，DB 與完整執行日誌證實作業已成功；未因此重跑。
- 實際服務 http://127.0.0.1:1688/strategies，Chrome 155.0.8059.40，沒有 ignore-certificate-errors。桌面 1440×1000 以 native mouse 點名稱：Hybrid、Defensive、Quality Growth 三卡，連結數 3／0／5；手機 390×844 加選 Mean Reversion，四卡、8 個股票連結。所有卡日期 10/13，無候選顯示「已完成篩選，無入選股票」。
- 4 個瀏覽器推薦請求 HTTP 200，uncaught errors=0、network failures=[]。真實點 6226 進入 /stock-analysis?id=6226；stock-analysis API 收盤 44.2、成交量 16,875,953、source_date=10/13、is_stale=false，圖表不含 10/09–10/11 異常列。
- 桌面／手機截圖已目視核對；路徑為 C:/Users/Norlan/AppData/Local/Temp/stock-backfill-desktop-20261014.png 與 stock-backfill-mobile-20261014.png。測試腳本為同目錄 stock-backfill-browser-check.py；截圖及 CDP 腳本是本機除錯產物，未加入版本庫。
- system-status 的 recommendation_freshness=current、market_date=recommendation_date=10/13。全站 DEGRADED 如實反映最近一次正式 daily 排程：10/13 update_database 失敗，run_daily／push_to_line 無同跑次紀錄。手動補檔不等於正式排程全流程成功；沒有混合不同跑次、覆寫失敗或發送 LINE。新增隔離 SQLite regression 確認手動成功不掩蓋正式失敗。

## OpenSpec verify-change

驗證對象明確為 `2026-10-13-backfill-market-data`，schema 為 spec-driven。已逐份核對 proposal、design、兩份 delta spec、tasks 與實作。OpenSpec 1.3.0 對日期開頭 ID 的 status／instructions 限制以直接讀取 artifacts 補足；未略過完整性、正確性或設計一致性核對。

| 面向 | 結果 |
| --- | --- |
| Completeness | 6/6 tasks；5/5 requirements |
| Correctness | 7/7 scenarios 有實作及回歸／真實操作證據 |
| Coherence | 沿用 canonical MCP、現有更新／推薦／Web 契約；保留原策略條件、相容 aliases、歷史異常及 production gate |

| Requirement／scenario | 實作與驗證證據 |
| --- | --- |
| Validate exchange dates／不同來源日期 | services/mcp/server.py:901；test/test_ingestion_source_contract.py:10；實際舊 TPEx 10/12 回 10/13 被拒絕 |
| Reject incomplete ingestion／單市場缺列 | services/mcp/server.py:1069、jobs/update_database.py:89；test/test_ingestion_source_contract.py:29、:63 |
| Reject incomplete ingestion／個別法人缺值 | jobs/update_database.py:156、:247；test/test_ingestion_source_contract.py:46 以 SQLite 驗證 NULL 與真實 0 |
| Use valid daily history／保留異常列 | jobs/run_daily.py:62；test/test_run_daily.py:90 乾淨／污染一致性；既有 deterministic characterization 驗證訊號、排序與 persistence |
| Validate margin enrichment／20 欄 tables | core/crawlers/chip_data_scraper.py:108、:214；test/test_ingestion_source_contract.py:110；兩日來源與餘額實際核對 |
| Persisted results end to end／補檔成功 | app/web_server.py:959、templates/strategy_center.html:36；七策略 live API、SQL snapshot、桌面／手機 CDP、股票連結 |
| Persisted results end to end／手動成功與正式失敗 | app/web_server.py:1178；test/test_dashboard_stock_snapshot.py:572；live system-status 顯示 current 與 degraded 的各自證據 |

### 最終回歸與檢查

```powershell
.\myenv\Scripts\python.exe -m pytest test/test_ingestion_source_contract.py test/test_phase2_chip_data.py test/test_market_data_integrity.py test/test_richmenu_mcp_server_routes.py test/test_strategy_center.py test/test_web_ui_integration.py test/test_web_strategy_chart_integration.py test/test_run_daily.py test/test_run_daily_persistence.py test/test_cli_dry_run.py test/test_backfill_pipeline.py test/characterization/test_daily_selection_deterministic.py test/test_dashboard_stock_snapshot.py test/test_pipeline_run_state.py test/test_web_shell_navigation.py -q
git diff --check
openspec validate 2026-10-13-backfill-market-data --strict --no-interactive
```

pytest：117 passed、3 xfailed、9 warnings。三個 xfailed 是既有舊 Dashboard tab layout／detail tabs／script characterization，非本次新增失敗；九個 warnings 為既有 pandas deprecation。

git diff --check 通過；OpenSpec strict validation 回傳 `Change '2026-10-13-backfill-market-data' is valid`。

CRITICAL：0。WARNING：0。SUGGESTION：0。本次補檔與 Web 串接規格驗證通過。

### 維運與驗收限制

- 正式排程最近跑次仍失敗，因此不能宣稱全流程已恢復或推播成功。
- 基本面策略 live API 的 freshness_status=blocked、has_persisted_snapshot=false。既有缺失 promotion／漂移證據、Fresh OOS 樣本不足與 gate 限制仍存在；資料补檔不替代策略績效驗收，未改凍結門檻或啟用 production。
- 10/09–10/11 原始異常歷史保留，只排除其對當日計算的影響。未刪除任何非快取檔案或改寫歷史策略 key。

## ponytail-review

核對本次 `be8aeba..HEAD` 與最終工作區 diff：移除無呼叫抓取器與重複 ETF 路徑、共用兩市完整性收尾，沿用既有資料庫、CLI、策略 registry 與 Web 接口；未新增依賴或推測性抽象。新增 regression 直接防止日期錯貼、資料缺值偽造、歷史污染與排程狀態誤報。

Lean already. Ship.
