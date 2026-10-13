# 2026-10-13 設計

## Context

官方 10/13 行情含 2,333 個支援代碼，26 個無正收盤價；TWSE T86 發布後兩市法人覆蓋 2,205 個代碼。覆蓋率不等於全部缺列為真實零值。資料庫尚缺 canonical dealer_buy 等欄位。

## Decisions

來源日期防護在實際補檔前證實 TPEx 舊 stk_quote_result.php 請求 10/12 卻回 date=20261013；新版 www/zh-tw/afterTrading/dailyQuotes 使用 date=2026/10/12 則正確。新行情表已包含 ETF。TPEx 法人表有 24 欄，自營商合計買賣超位於 22，舊 16 欄僅自行買賣。

0. 改用已核對日期的 TPEx dailyQuotes；沿用 canonical include_etfs 過濾，移除重複 ETF HTTP 抓取。法人使用已確認的第 22 欄合計，未知結構拒絕。
1. 在 canonical MCP 官方解析處驗證 payload 日期，ALL 任一市場缺資料時拒絕部分結果。兩市均空仍可表示無資料日。
2. 更新器遇到非空行情及失敗／空法人資料時拒絕寫入。缺少個別代碼的法人欄位保留 NULL，不推定零。
3. 使用現有 update_market_date 補 10/12、10/13；只新增所需 nullable 欄位。既有 10/09–10/11 異常紀錄保留。
4. 用 run_daily --date 2026-10-13 刷新七策略；驗證候選或 heartbeat。維持基本面 production gate。
   Daily 的歷史讀取原本含 10/09 無市場錨點及週末污染列；計算前以既有市場代表股日期和工作日規則篩選，排除權證及重複鍵。修改前 daily characterization／CLI／persistence 基線 14 passed；乾淨輸入須維持浮點 rtol=1e-9、atol=1e-12。
5. 在實際 127.0.0.1:1688 Web 操作名稱／checkbox 並比對 API 與顯示。用既有測試檢查錯誤與空結果。
6. Audit 以引用證據判斷；相容代理不視為可直接刪除。簡化不觸及策略或回測規則。

## Verification

保留前後日期／筆數、來源日期、缺值與重複鍵檢查。執行相關 pytest、真實瀏覽器驗證、git diff --check、OpenSpec strict validation；最後依 requirements 與 tasks 進行 verify-change。
