# File Reference Map

> 盤點日期：2026-07-22。每項均先以保守原則處理：未能涵蓋外部使用者、排程與歷史資料的檔案標記 `UNKNOWN` 或 `LEGACY_COMPATIBILITY`，不刪除。

| 對象 | 分類 | 證據指令 | 結果摘要 | 結論 |
|---|---|---|---|---|
| `4_run_backtest.py` | LEGACY_COMPATIBILITY | `rg -n "4_run_backtest" app execution jobs test README.md doc` | `app/__init__.py:270` 動態 import；測試與歷史文件均引用 | 保留 |
| `5_push_to_line.py` | LEGACY_COMPATIBILITY | `rg -n "5_push_to_line" app execution jobs test README.md doc` | `execution/morning_run.bat:16` 使用；文件與測試保留契約 | 保留 |
| `tool/**` | LEGACY_COMPATIBILITY | `rg -n "from tool|import tool|tool\\." --glob '*.py' --glob '*.bat' --glob '*.md'` | `tool/_proxy.py` 與各 module wrapper 都轉向 `core.*`；外部 script 相容性無法由 repo 證明 | 保留 |
| `specs/**` | DEVELOPMENT_TOOLING | `rg -n "specs/|001-|002-|003-|004-" README.md doc openspec .github` | 歷史規格文件，未完成遷移決策 | 保留 |
| `doc/**` | DEVELOPMENT_TOOLING | `rg -n "doc/|DASHBOARD_FIX_GUIDE|UpdateList" README.md .github openspec` | 維運與歷史變更證據；文件本身也記錄舊入口 | 保留 |
| `core/viz_helper.py`、`core/report_helper.py` | UNKNOWN | `rg -n "viz_helper|report_helper" app core jobs test` | 有現存程式引用；與未來模組重疊尚未確認 | 保留，Phase 9 再判定 |
| `__pycache__/`、`.pytest_cache/`、`.coverage` | CACHE | `git check-ignore -v __pycache__ .pytest_cache .coverage` | 已被忽略，無原始碼契約 | Phase 3 可清理 |
| `ML_Data/`、本機回測輸出 | GENERATED_ARTIFACT / UNKNOWN | `git check-ignore -v ML_Data artifacts` | 大型資料及產物忽略；資料是否可重建尚未盤點 | 不刪除，先移至 artifacts |
| `stop`、`docs/cleanup_*.md` | UNKNOWN | `git status --short` | 已在工作樹缺失，屬既有使用者變更 | 不修改 |

檢查範圍已包含 Python import、字串引用、CLI、bat、docker-compose、scheduler、README、文件、測試、Web／LINE 入口、環境設定、Agent 目錄與 OpenSpec。未列為可刪除者即無完整無引用證據。
