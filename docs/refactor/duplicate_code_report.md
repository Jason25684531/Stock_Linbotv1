# Duplicate Code Report

| 範圍 | 證據 | 判定 | 行動 |
|---|---|---|---|
| `tool/**` 與 `core/**` | `tool/_proxy.py` 將 legacy module 指到 canonical `core.*`；各 wrapper 僅有 proxy 呼叫 | 刻意的相容 facade，不是死碼 | 保留至相容期結束 |
| 根目錄 `4_run_backtest.py` 與 `jobs/run_backtest.py` | 根目錄檔只 `import_module('jobs.run_backtest')` | 刻意的相容 wrapper，不是重複回測邏輯 | Phase 4 補 deprecation metadata |
| 根目錄 `5_push_to_line.py` 與 `jobs/push_to_line.py` | 根目錄檔只 `import_module('jobs.push_to_line')` | 刻意的相容 wrapper，不是重複推播邏輯 | Phase 4 補 deprecation metadata |
| `core/viz_helper.py`、`core/report_helper.py` 與計畫中的 `core/visualization/` | 新模組尚未建立，且現有 helper 仍被引用 | UNKNOWN，不能以名稱推斷重複 | Phase 9 以 import 與功能盤點決定 |
| `BacktestEngine` 與 `PortfolioBacktestEngine` | 同位於 `jobs/run_backtest.py`，提案已記錄績效責任重疊 | 已知重構目標，不在清理階段直接刪除 | Phase 5 先以 characterization tests 保護後拆分 |
