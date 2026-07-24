# Agent Working Rules

## OpenSpec

- 變更目錄使用 `YYYY-MM-DD-<change-name>`；任務標題與日期使用 ISO 8601 日期。
- 先閱讀 proposal、design、specs 與 tasks，再修改實作；完成任務後立刻勾選對應 checkbox。
- 規格、設計與實作發生矛盾時，先以程式碼證據更新文件，再進行受影響的實作。

## Safety

- 不刪除非快取檔案，除非 `docs/refactor/deletion_candidates.md` 有完整無引用證據。
- 保留使用者既有未提交變更；不得以 reset、checkout 或批次覆寫清理工作區。
- 機密只由環境變數或 `.env` 載入，不寫入程式、測試或文件。

## Backtesting

- 修改回測核心或策略前，必須先建立並通過 characterization baseline。
- 結構重構不得改變訊號、成交時點、成本、滑價或策略條件。
- 所有未定義績效指標以 `value=None` 與不可計算原因表達，禁止以 0 或 infinity 偽裝。

## Compatibility

- `4_run_backtest.py`、`5_push_to_line.py`、舊策略 key 均為相容面；未有明確移除證據前不得刪除。
- 舊策略 key 經 alias 解析；不得改寫歷史資料庫紀錄。
- CLI、Web、LINE Bot 與 `jobs/scheduler.py` 的公開行為需維持相容。

## Verification

- 交易日期、股票代碼、方向、順序與數量必須完全一致；浮點比較使用 `rtol=1e-9` 與 `atol=1e-12`。
- 每個 Phase 保留可獨立 revert 的提交與驗證證據。
- 完成後執行相關測試、`git diff --check` 與 OpenSpec strict validation。
