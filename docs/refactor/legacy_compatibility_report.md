# Legacy Compatibility Report

| 相容面 | Canonical 目標 | 現況 | 風險／預計處理 |
|---|---|---|---|
| `4_run_backtest.py` | `jobs.run_backtest` | wrapper 在 `__main__` 執行 canonical `main()`；`app/__init__.py:270` 仍載入 | Phase 4 加入 Deprecated、預計移除版本與新入口資訊；不搬移 |
| `5_push_to_line.py` | `jobs.push_to_line` | wrapper 在 `__main__` 執行 canonical `main()`；`execution/morning_run.bat:16` 仍呼叫 | Phase 4 同上，並先將 bat 改為 canonical scheduler 再考慮淘汰 |
| 策略 key `v31`、`v33`–`v38` | 尚待 Phase 6 事實稽核後的新 id | registry、Web、LINE、設定、DB 與文件皆散落引用 | 只能新增 alias 層；不得改寫歷史資料 |
| `specs/`、`doc/` | 未定 | 仍保存歷史規格與維運記錄 | Phase 1 只標記；需遷移計畫才能處理 |

目前所有相容入口都維持可用；本 Phase 未搬移或移除任何檔案。

## 入口對照

| 舊入口 | Canonical 入口 | 狀態 |
|---|---|---|
| `4_run_backtest.py` | `jobs/run_backtest.py` | 保留 wrapper 至 v4.0 |
| `5_push_to_line.py` | `jobs/push_to_line.py` | 保留 wrapper 至 v4.0 |
| `execution/daily_run.bat` | `jobs/scheduler.py daily` | 已指向 canonical |
| `execution/evening_run.bat` | `jobs/scheduler.py evening --stop-on-error` | 已指向 canonical |
| `execution/morning_run.bat` | `jobs/scheduler.py morning` | 已改為 canonical |
| `execution/run_manual.bat` | `jobs/scheduler.py <target>` | 已指向 canonical |
