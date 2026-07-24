# Repository Inventory

> 盤點日期：2026-07-22  
> 範圍：盤點開始時工作樹中 219 個追蹤檔與 2 個未追蹤、未被忽略檔。`docs/refactor/` 與 `AGENTS.md` 為本 Phase 新增文件，分類為 ACTIVE。

| 分類 | 路徑 | 數量 | 判定 |
|---|---|---:|---|
| ACTIVE | `app/**`, `app.py` | 4 | Web、LINE 與應用入口 |
| ACTIVE | `config/**`, `config.py`, `.env.example` | 4 | 執行設定與設定範例 |
| ACTIVE | `core/**` | 28 | 領域邏輯、策略、資料存取與報表 |
| ACTIVE | `jobs/**`, `execution/**` | 15 | 批次工作與 Windows 排程入口 |
| ACTIVE | `services/**`, `scripts/**` | 6 | MCP 服務與維運工具 |
| ACTIVE | `templates/**`, `Richmenu/Richmenu.png` | 6 | Web 與 LINE 靜態資產 |
| ACTIVE | `test/**`, `pytest.ini` | 35 | 自動化測試與 pytest 設定 |
| ACTIVE | `strategy_settings.json`, `docker-compose.yaml`, `.dockerignore`, `init_settings.py`, `requirements*.txt`, `README.md`, `.gitignore` | 10 | 執行、部署與專案設定 |
| ACTIVE | `doc/SERVICE_RESTART_RUNBOOK.md`, `execution/restart_services.bat` | 2 | 未追蹤的維運文件／腳本；保留且不在本 Change 修改 |
| LEGACY_COMPATIBILITY | `4_run_backtest.py`, `5_push_to_line.py` | 2 | 根目錄舊入口，轉呼叫 `jobs.*` |
| DEVELOPMENT_TOOLING | `.claude/**`, `.github/**`, `openspec/**` | 48 | Agent、CI 與正式 OpenSpec 工作流 |
| DEVELOPMENT_TOOLING | `specs/**` | 28 | 歷史 spec-kit 規格；待遷移方案，非刪除候選 |
| DEVELOPMENT_TOOLING | `doc/**`（既有 5 檔） | 5 | 歷史操作／變更文件；待盤點遷移 |

總計 221 個盤點對象；各列路徑模式完整覆蓋盤點時所有非忽略檔。`__pycache__/`、`.pytest_cache/`、`.coverage`、`ML_Data/`、虛擬環境與 Agent 本機狀態均由 `.gitignore` 排除，不納入此清單。

## 特別目錄

| 路徑 | 分類 | 結論 |
|---|---|---|
| `specs/` | DEVELOPMENT_TOOLING | 28 份歷史編號規格；僅標記，Phase 1 不刪。 |
| `doc/` | DEVELOPMENT_TOOLING | 既有操作與歷史報告；提出遷移方案前保留。 |
| 根目錄 wrappers | LEGACY_COMPATIBILITY | 仍被 `app/__init__.py`、批次檔、測試與文件引用；保留。 |
| `core/viz_helper.py`、`core/report_helper.py` | UNKNOWN | 與未來 `core/visualization/` 可能重疊；待 Phase 9 盤點。 |
| `stop`、`docs/cleanup_*.md` | UNKNOWN | 已在工作樹缺失且屬既有未提交刪除；不由本 Change 復原或刪除。 |
# Visualization overlap decision (2026-07-22)

`core/viz_helper.py` remains the legacy dashboard helper. `core/visualization/`
contains only prepared-data stability charts and does not import the engine; the two
are intentionally separate until dashboard migration has a dedicated characterization baseline.
