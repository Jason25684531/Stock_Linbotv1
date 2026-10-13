# 2026-10-13 策略中心預覽修正與驗收檢驗

## Why

使用者點策略後沒有明顯反應。`templates/strategy_center.html` 的股票列表讀取 `results[id].data.recommendations`，但 fetch 儲存完整 API envelope，股票實際在 `results[id].data.data.recommendations`。策略名稱沒有選取動作，結果區位於長目錄下方。HTTP 200 的應用錯誤也會被誤認為缺少快照。

## What Changes

- 修正股票列表 envelope 路徑與應用錯誤處理。
- 以原生 label 讓桌面與手機策略名稱可勾選，將結果區放到目錄上方。
- 說明預覽、Daily 與回測用途，以及可執行狀態和績效驗收的差別。
- 記錄目前策略验收門檻、實際資料狀態、證據缺口及瀏覽器驗證。

## Capabilities

### Modified Capabilities

- `strategy-stock-results`: 正確渲染股票連結及 HTTP 200 應用錯誤，名稱點选觸發同一預覽流程。

## Impact

變更限於策略中心模板、既有 UI 回歸測試與檢驗文件。沿用推薦 API、canonical identity 與 Daily／回測相容流程。
