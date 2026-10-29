# Stock AI Line Bot

台股研究與每日選股推薦系統，有四個執行入口：Web 儀表板、LINE Bot、命令列 jobs、中央排程器 `jobs/scheduler.py`。正式環境以 Docker Compose 執行，使用 Python 3.11；本機開發請用同一個版本。

日常操作（重啟、更新資料、補缺口、排錯）請看 [`docs/operations_runbook.md`](docs/operations_runbook.md)。

## 系統架構

```text
TWSE / MOPS / 新聞
   │
   ├─ services/mcp/server.py ── MCP HTTP 服務 :8080（市場統計、法人、公司資料等 tools）
   │        ▲
   │        │ core/mcp_client.py
   ▼        │
jobs/scheduler.py ── Windows 工作排程器 08:30 morning / 18:30 evening（在主機上執行）
   │  update_database → run_daily → daily_backtest_validation → push_to_line
   ▼
MySQL :3306（daily_market_data、daily_recommendations、pipeline_runs …）
   │
   ▼
app/ ── Flask :1688（Web 頁面、JSON API、LINE webhook /callback）
```

| 服務 | Compose 名稱 | 埠 | 說明 |
|---|---|---|---|
| MySQL 8.0 | `db` | 3306 | 資料儲存在 volume `mysql_data` |
| MCP 服務 | `twse_mcp_server` | 8080 | 對外抓資料的唯一邊界 |
| Web／LINE Bot | `stock_bot` | 1688 | `python app.py` |
| 排程 | 不在 Compose 中 | — | 主機 `myenv` 透過 `localhost` 連到上面三個服務 |

## 目錄與職責

| 路徑 | 職責 |
|---|---|
| `app/__init__.py` | 組合根與相容門面。`create_app()`、`from app import app` 和 `python app.py` 都可用 |
| `app/web_server.py` | Web 路由與 JSON 契約 |
| `app/line_bot.py`、`line_flows.py`、`line_commands.py` | LINE webhook、postback、指令比對、Flex 訊息組裝 |
| `app/dashboard_payloads.py` | 健康檢查、總經、市場快照、MCP overlay 與本地 fallback |
| `app/news_overlay.py` | 新聞情緒資訊與降級處理 |
| `config.py`、`config/settings.py` | 所有設定與常數，從環境變數讀取 |
| `core/db_helper.py` | 資料庫存取（其他模組不寫原始 SQL） |
| `core/mcp_client.py` | MCP 傳輸、重試、逾時 |
| `core/strategy/` | `StrategyRegistry`（策略身分）、`StrategyRunner`（執行）、legacy adapter |
| `core/strategies/` | 各策略實作，包含舊版 `v31`–`v38` 模組 |
| `core/strategy_manager.py` | 設定、啟用策略與相容門面（`strategy_settings.json`） |
| `core/backtest/` | 回測引擎、成本、滑價、績效指標 |
| `core/runtime/` | Fundamental 策略的身分凍結、shadow、Fresh OOS 與上線閘門 |
| `core/research/`、`core/validation/` | 因子研究、PIT 資料、walk-forward 與穩健性驗證 |
| `core/crawlers/`、`core/update_*.py` | 融資融券、季財報、月營收補充資料 |
| `services/mcp/server.py` | MCP HTTP 服務（`scripts/twse_mcp_server.py` 是相容啟動器） |
| `jobs/` | 正式 CLI 入口；研究類 `run_*` 都只能明確指定執行 |
| `execution/*.bat` | Windows 包裝：排程、手動選單、重啟 |
| `templates/`、`static/` | Web 前端（規範見 `openspec/specs/frontend-design.md`） |

## 執行模式：Research 與 Production

預設為 `RESEARCH`。入口啟動時會在 stderr 顯示 `MODE: ... | GATES: ...`。

| Operation | Research | Production |
|---|---|---|
| 歷史回測、參數掃描、本機分析、報告 | ALLOW（不需 grant，次數不限） | ALLOW（不需 grant） |
| 已登記實驗（固定參數、開發區間） | ALLOW，經 `admit_experiment` 記錄試驗 | ALLOW |
| 新策略研究 | ALLOW | ALLOW |
| 核准公開研究資料 | 免 grant；白名單＋固定預算＋限速＋稽核；未分配預算拒絕 | 保留適用可信 grant、scope／quota |
| 正式 DB 寫入、LINE 推播、排程建立 | DENY（強制後） | REQUIRE GRANT |
| live order、修改授權、改 Production 額度或策略範圍 | DENY | DENY（`forbidden`，無 grant 可開） |
| 未知 operation | DENY | DENY |

- `PRODUCTION` 需要同時具備 `STOCK_LINBOT_MODE=production`、repo 外且 Owner 控制的可信授權檔、未過期的 `production_mode` grant；環境變數單獨無效。設定方式見 [`docs/research/production_grant_setup.md`](docs/research/production_grant_setup.md)，驗證用 `python scripts/verify_production_grant.py`（唯讀）。
- DB 寫入、LINE 推播保留既有 Audit-only／STOCK_LINBOT_ENFORCE 階段。**Research 外部 HTTP 永遠強制**，不需要另設 enforcement env。
- 公開 Research 固定預算：OD-P6 385、T1b 10、2026 日曆1；SQLite 持久化，環境變數不能提額。無預算的 MOPS／Daily 拒絕新請求，既有快取可用。見 [操作說明](docs/research/public_data_access.md)。
- 試驗管理：`scripts/research_workflow.py` 的 `admit_experiment` 對每次已登記實驗寫入 `outputs/research_workflow/trial_ledger.jsonl`；相同實驗重跑為 `REPLAY`（不限次數、不增加試驗數），新設定為 `TRIAL`（受登記的 `trial_budget` 限制並計入 DSR 試驗數 743 之上）。

## 快速開始

從 `.env.example` 複製出 `.env`，所有機密只放在環境變數或 `.env`。Compose 至少需要 `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`FLASK_SECRET_KEY`；`LINE_TOKEN`、`LINE_SECRET`、`GEMINI_KEY`、`GEMINI_MODEL` 為選填。應用程式一律以 MySQL 的 trader user 連線，不用 root。主機上執行 jobs 時的連線範例：

```text
DB_URL=mysql+pymysql://trader:<password>@localhost:3306/stock_ai_db
MCP_BASE_URL=http://localhost:8080
MODEL_PATH=ML_Data/pkl/stock_ai_model.pkl
```

```powershell
docker compose up -d db twse_mcp_server stock_bot
docker compose ps

python -m venv myenv
.\myenv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.runtime.txt` 是 `stock_bot` 的依賴清單，`requirements.mcp.txt` 是 `twse_mcp_server` 的依賴清單。`requirements.txt` 是開發與研究環境，不屬於服務契約。容器啟動時才會 `pip install`，所以第一次啟動或重啟可能要好幾分鐘。

## 每日資料流程

正式排程統一使用 `jobs/scheduler.py`，不要另外建立第二套 scheduler。

```powershell
python jobs/scheduler.py daily            # 或 evening（排程器使用 evening --stop-on-error）
python jobs/scheduler.py daily --dry-run  # 預覽，不寫入、不推播
```

pipeline 依序執行：

`jobs/update_database.py` -> `jobs/run_daily.py` -> `jobs/run_daily_backtest_validation.py` -> `jobs/push_to_line.py`

- `update_database`：透過 MCP 抓股價與法人資料，在本地補上月營收、季財報、融資融券，最後預熱 Dashboard 快取。
- `run_daily`：執行啟用中的策略，寫入 `daily_recommendations`。同一天即使沒有候選股，也會寫一筆 heartbeat。
- 每個步驟的狀態都記錄在 `pipeline_runs`，Web 的系統頁會讀取它。
- `daily_backtest_validation` 是輕量檢查，由 `ENABLE_DAILY_BACKTEST_VALIDATION`（預設關閉）和 `DAILY_BACKTEST_WINDOW_DAYS`（預設 60）控制。它只對小範圍股票做短窗口驗證，is not full historical research backtesting and not parameter optimization。
- 失敗會回傳非 0 exit code；搭配 `--stop-on-error` 時，後面的步驟不會執行。

其他只能明確指定執行的 job：

```powershell
python jobs/scheduler.py update_database           # 只更新資料
python jobs/scheduler.py push_evening              # 只推播
python jobs/scheduler.py backfill --dry-run        # 列出市場／推薦缺口
python jobs/scheduler.py backfill -- --start-date 2026-10-01
python jobs/scheduler.py fundamental_shadow_operation
```

推薦和價格的來源欄位會一路保留到最後：`price_trade_date`、`price_source_date`、`price_basis`、`price_data_source`、`price_is_stale`、`recommendation_close_price`、`recommendation_trade_date`、`recommendation_price_basis`、`recommendation_is_stale`、`trade_date`、`source_date`、`data_source`、`is_stale`。

## 策略平台

- `StrategyRegistry` 是策略身分的唯一來源，負責解析 canonical ID 和舊版 alias。`StrategyRunner` 負責執行。`StrategyManager` 是設定與相容門面，不是另一套身分來源。
- 目前的 canonical 策略：`hybrid_trend_rank`、`defensive_low_volatility`、`growth_momentum_breakout`、`quality_growth`、`institutional_flow_confirmation`、`mean_reversion`、`quality_value_low_volatility`。啟用中的策略記錄在 `strategy_settings.json`。
- 舊策略 key（例如 `v34_turbo`、`v35_innovation`）透過 alias 解析。改顯示名稱或 metadata 時，**不得改寫歷史資料庫紀錄**。
- Fundamental（`fundamental_g2g3_top5_reb60_score_weighted_v1`）和 legacy 策略分開執行。它的身分已凍結，PIT／Fresh OOS 閘門由 `core/runtime/` 強制執行，未通過時一律封鎖。在 `ENABLE_FUNDAMENTAL_PRODUCTION` 開啟且閘門通過之前，它只會以 shadow 狀態顯示在 Strategy Center，不進入推薦和推播。下單功能永遠關閉：

```text
BROKER_ORDER_SUBMISSION=DISABLED
```

## Web、API 與健康檢查

- 頁面：`/dashboard`、`/strategies`、`/stock-analysis`、`/backtest`、`/system`、`/login`。
- 主要 API：`/api/daily-signals`、`/api/market/*`、`/api/stock-analysis`、`/api/strategies`、`/api/backtest/run`、`/api/performance`。
- 健康檢查：兩個服務都有 `/health`，Compose healthcheck 和重啟腳本都用它。`/api/dashboard/health-check?symbol=2330` 是個股健檢 API，not the container health endpoint。
- 服務不可用時，回應會明確標示 degraded／fallback 狀態；無法計算的績效以 `value=None` 加上原因表示，不用 `0` 或 infinity 代替。

## 重啟與維運

```bat
execution\restart_services.bat --docker   :: 停掉本機衝突程序，重啟 MCP + Web 容器並等待 /health
execution\run_manual.bat                  :: 手動選單：推播、更新、選股、補缺口
```

完整步驟見 [`docs/operations_runbook.md`](docs/operations_runbook.md)。

## 相容面（不得隨意移除）

`app.py`、`config.py`、`4_run_backtest.py`、`5_push_to_line.py`、`scripts/twse_mcp_server.py`、`execution/*.bat`、舊策略 alias，以及 CLI、Web、LINE 和 scheduler 的公開行為。

```powershell
python app.py
python jobs/run_backtest.py --strategies all --days 365 --mode balanced
python 4_run_backtest.py --help
python 5_push_to_line.py --help
```

## 資料與產物

- `data/processed/`：不可變的研究證據與 manifest。
- `outputs/`：可重現的 job 輸出與 runtime 證據（包含 Fundamental shadow／OOS）。
- `artifacts/`：審查報告、回測產物與可續跑的來源快取。
- `docs/refactor/`：目前的架構、依賴、相容性與刪除決策。
- `openspec/`：規格（`specs/`）與變更（`changes/`，完成後移到 `archive/`）。只保留在本機，不進 git。

刪除非快取檔案前，必須在 [`docs/refactor/deletion_candidates.md`](docs/refactor/deletion_candidates.md) 完成 `SAFE_TO_REMOVE` 紀錄。引用狀態不明時，一律保留（KEEP）。

## 驗證

```powershell
python -m compileall -q app core jobs
python -c "import app; print(app.create_app())"
python -m pytest -q --timeout=10 test/test_daily_pipeline_consolidation_contract.py test/unit/runtime test/test_cli_dry_run.py
python -m pytest -q                       # 完整測試，約 4 分鐘
git diff --check
openspec validate <change-id> --strict --no-interactive
```

需要外部 MCP 或新聞服務的測試，請先啟動對應服務，或使用測試內建的 fixture。

延伸閱讀：[`docs/refactor/architecture-boundary-health_2026-10-06.md`](docs/refactor/architecture-boundary-health_2026-10-06.md)、[`docs/refactor/strategy-platform-runtime-contract-2026-10-06.md`](docs/refactor/strategy-platform-runtime-contract-2026-10-06.md)、[`docs/fundamental_daily_operations.md`](docs/fundamental_daily_operations.md)、[`docs/strategy_operations.md`](docs/strategy_operations.md)。
