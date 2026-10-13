# 2026-10-13 市場補檔與 Web 串接驗證

## Why

有效行情停在 2026-10-08，七個策略推薦停在 2026-06-26。2026-10-13 TWSE T86 曾尚未發布，MCP 將單一市場結果當成 ALL，更新器再將未取得法人資料補成 0。來源日期也只使用請求日期，無法拒絕 TPEx 回退資料。

## What Changes

- 驗證官方來源日期及 ALL 兩市場完整性，保留法人缺值。
- 使用既有逐日更新入口補 2026-10-12、2026-10-13，刷新當日七策略推薦。
- 驗證實際 Web API、策略勾選、股票連結及日期顯示。
- 完成全庫複雜度 audit、ponytail debt/gain 與本次 diff review，處理有證據的簡化。

## Capabilities

### Modified Capabilities
- `market-data-integrity-repair`: 擷取邊界驗證來源日期、拒絕部分市場、保留未取得法人數據。
- `strategy-stock-results`: 驗證補檔後正式 API 與 Web 的當日推薦及 heartbeat。

## Impact

使用現有 MCP、逐日更新、推薦落庫與 Web 契約。保留歷史異常列與舊策略 aliases；不更動策略條件。僅生成 2026-10-13 推薦，避免將今日新聞套入歷史推薦。
