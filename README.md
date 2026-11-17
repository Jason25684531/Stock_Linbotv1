# Stock AI Line Bot

台股研究與每日選股推薦系統。常駐服務（MySQL、MCP、Web／LINE Bot）以 Docker Compose 執行；每日資料流程由主機上的 Windows 工作排程器呼叫 `jobs/scheduler.py`。Python 版本為 3.11，本機開發也請用同一版。

日常維運（重啟、更新資料、補缺口、排錯）的完整步驟見 [`docs/operations_runbook.md`](docs/operations_runbook.md)。

---

## 1. 啟動專案（每次開機）

**Web 入口：<http://localhost:1688>**（`/` 會導向 `/dashboard`）

| 步驟 | 動作 | 成功的樣子 |
|---|---|---|
| 1 | 開啟 Docker Desktop | 系統匣顯示 Docker 已執行 |
| 2 | 在 repo 根目錄執行 `docker compose up -d` | 三個容器建立或啟動 |
| 3 | `docker compose ps` | `db`、`twse_mcp_server`、`stock_bot` 都是 `healthy` |
| 4 | 瀏覽器開啟 <http://localhost:1688/dashboard> | 看到儀表板 |

- 容器設定為 `restart: always`（db）與 `unless-stopped`（MCP、Web）。上次關機前如果容器還在執行，開啟 Docker Desktop 後會自動啟動，步驟 2 只是保險。
- MCP 和 Web 容器每次啟動都會重新 `pip install`，要等 **5～20 分鐘** 才會變成 `healthy`。這段期間狀態會顯示 `health: starting`，屬於正常情況。
- 每日排程（08:30／18:30）在主機上執行，不需要手動啟動。前提是容器已經 healthy，而且電腦在排程時間是開機狀態。

### 確認服務正常

```powershell
docker compose ps
curl http://127.0.0.1:1688/health    # Web／LINE Bot
curl http://127.0.0.1:8080/health    # MCP
```

失敗時查看 log：

```powershell
docker logs stock_ai_stock_bot --tail 30
docker logs twse_mcp_server --tail 30
docker logs stock_ai_mysql --tail 30
```

### 服務與埠

| 服務 | Compose 名稱 | 容器名稱 | 埠 | 健康檢查 |
|---|---|---|---|---|
| Web／LINE Bot（`python app.py`） | `stock_bot` | `stock_ai_stock_bot` | **1688** | `/health` |
| MCP 市場資料服務 | `twse_mcp_server` | `twse_mcp_server` | 8080 | `/health` |
| MySQL 8.0（volume `mysql_data`） | `db` | `stock_ai_mysql` | 3306 | `mysqladmin ping` |
| 每日排程 | 不在 Compose 中 | — | — | 工作排程器的「上次執行結果」 |

`stock_bot` 會等 `db` 和 `twse_mcp_server` 都 healthy 後才啟動。

### Web 頁面

| 路徑 | 內容 |
|---|---|
| `/dashboard` | 儀表板（`/`、`/login` 都會導向這裡） |
| `/strategies` | 策略中心 |
| `/stock-analysis` | 個股分析 |
| `/backtest` | 回測 |
| `/intelligence` | 策略情報平台 |
| `/platform` | 研究平台 V2 |
| `/reference` | 參考研究比較 |
| `/system` | 系統狀態（讀取 `pipeline_runs`） |
| `/callback` | LINE webhook（POST） |

主要 API：`/api/daily-signals`、`/api/market/*`、`/api/stock-analysis`、`/api/strategies`、`/api/backtest/run`、`/api/performance`。`/api/dashboard/health-check?symbol=2330` 是個股健檢 API，不是容器的健康檢查端點 (not the container health endpoint)。

---

## 2. 首次安裝（新機器或重建環境）

1. **建立 `.env`**：從 `.env.example` 複製出 `.env`。所有機密只放在環境變數或 `.env`，不寫進程式或文件。

   | 變數 | 必要性 | 說明 |
   |---|---|---|
   | `MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`FLASK_SECRET_KEY` | 必填 | Compose 缺少時拒絕啟動 |
   | `LINE_TOKEN`、`LINE_SECRET` | 選填 | LINE Bot 與推播 |
   | `GEMINI_KEY`、`GEMINI_MODEL` | 選填 | 早晨新聞摘要 |
   | `DB_URL`、`MCP_BASE_URL`、`MODEL_PATH` | 主機 jobs 用 | 見下方範例 |

   主機上執行 jobs 時，透過 `localhost` 連到容器。應用程式一律以 MySQL 的 trader user 連線，不用 root：

   ```text
   DB_URL=mysql+pymysql://trader:<password>@localhost:3306/stock_ai_db
   MCP_BASE_URL=http://localhost:8080
   MODEL_PATH=ML_Data/pkl/stock_ai_model.pkl
   ```

   容器內的 `DB_URL`、`MCP_BASE_URL` 由 `docker-compose.yaml` 自動設定為內部網路位址，不讀上面這兩行。

2. **啟動容器**：

   ```powershell
   docker compose up -d db twse_mcp_server stock_bot
   docker compose ps
   ```

3. **建立主機 Python 環境**（排程和 CLI 要用）：

   ```powershell
   python -m venv myenv
   .\myenv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

   | 依賴檔 | 用途 |
   |---|---|
   | `requirements.txt` | 主機開發、研究與排程 |
   | `requirements.runtime.txt` | `stock_bot` 容器 |
   | `requirements.mcp.txt` | `twse_mcp_server` 容器 |

4. **初始化設定表**（只需一次）：

   ```powershell
   python init_settings.py   # 建立並填入 user_settings，LINE 指令「查看設定」會讀這張表
   ```

5. **設定 Windows 工作排程器**：

   | 工作 | 時間 | 執行 |
   |---|---|---|
   | `*_Morning` | 08:30 | `execution\morning_run.bat` |
   | `*_Evening` | 18:30 | `execution\evening_run.bat` |

   批次檔內寫死了 repo 路徑 `D:\01_Project\Stocke\Stock_Linbotv1` 和 `myenv`。搬移 repo 時要一併修改。

---

## 3. 系統架構

```text
TWSE / MOPS / 新聞
   │
   ├─ core/runtime/public_data.py ── 公開資料請求的授權與稽核
   ├─ services/mcp/server.py ────── MCP HTTP 服務 :8080（部分市場資料路徑）
   ▼
jobs/scheduler.py ── Windows 工作排程器 08:30 morning / 18:30 evening（主機執行）
   │  update_database → run_daily → daily_backtest_validation → push_to_line
   ▼
MySQL :3306（daily_market_data、daily_recommendations、pipeline_runs …）
   │
   ▼
app/ ── Flask :1688（Web 頁面、JSON API、LINE webhook /callback）
```

系統有四個執行入口：Web 儀表板、LINE Bot、命令列 jobs、中央排程器 `jobs/scheduler.py`。

---

## 4. 每日資料流程

正式排程統一使用 `jobs/scheduler.py`，不要另外建立第二套 scheduler。

| Pipeline | 由誰觸發 | 步驟 |
|---|---|---|
| `morning` | `execution\morning_run.bat`（08:30） | `push_to_line --time morning` |
| `evening` | `execution\evening_run.bat`（18:30，加 `--stop-on-error`） | `update_database` → `run_daily` → `daily_backtest_validation` → `push_to_line --time evening` |
| `daily` | `execution\daily_run.bat`（相容用） | 與 `evening` 相同 |
| `push_evening` | 手動 | 只推播 |
| `research` | 手動 | `jobs/run_research_platform.py`，只寫 `outputs/platform_v2`，不碰 MySQL 和 LINE |

各步驟說明：

- `jobs/update_database.py`：依市場範圍與資料來源更新股價、法人、月營收、季財報及融資融券，最後預熱 Dashboard 快取。
- `jobs/run_daily.py`：執行啟用中的策略，寫入 `daily_recommendations`。當天沒有候選股時也會寫一筆 heartbeat。
- `jobs/run_daily_backtest_validation.py`：輕量檢查，由 `ENABLE_DAILY_BACKTEST_VALIDATION`（預設關閉）和 `DAILY_BACKTEST_WINDOW_DAYS`（預設 60）控制。它只對小範圍股票做短窗口驗證，不是完整歷史回測，也不是參數最佳化 (not full historical research backtesting, not parameter optimization)。
- `jobs/push_to_line.py`：LINE 推播。

`daily` 的執行順序：`jobs/update_database.py` -> `jobs/run_daily.py` -> `jobs/run_daily_backtest_validation.py` -> `jobs/push_to_line.py`

其他行為：

- 每個步驟的狀態都記錄在 `pipeline_runs`，Web 的 `/system` 頁會讀取。
- 失敗會回傳非 0 的 exit code；加上 `--stop-on-error` 時，後面的步驟不會執行。
- 正式 pipeline 會取得 `outputs/scheduler/production_pipeline.lock`。如果已有一個在執行，會印出 `PIPELINE_ALREADY_RUNNING`，並以 exit code 75 結束。
- 推薦與價格的來源欄位會一路保留：`price_trade_date`、`price_source_date`、`price_basis`、`price_data_source`、`price_is_stale`、`recommendation_close_price`、`recommendation_trade_date`、`recommendation_price_basis`、`recommendation_is_stale`、`trade_date`、`source_date`、`data_source`、`is_stale`。

### 手動執行

```powershell
python jobs/scheduler.py evening --stop-on-error     # 完整流程
python jobs/scheduler.py daily --dry-run             # 預覽，不寫入、不推播
python jobs/scheduler.py update_database             # 只更新資料
python jobs/scheduler.py push_evening                # 只推播
python jobs/scheduler.py backfill --dry-run          # 列出市場／推薦缺口
python jobs/scheduler.py backfill -- --start-date 2026-10-01
python jobs/scheduler.py fundamental_shadow_operation
```

也可以用選單 `execution\run_manual.bat`：

| 選項 | 動作 |
|---|---|
| 1 | Morning 推播 |
| 2 | Evening 完整流程 |
| 3 | 只推播 |
| 4 | 只更新 DB |
| 5 | 只選股（不推播） |
| 6 | 本機啟動 Web |
| 7 | 補缺口 |

排程某天失敗或當時電腦沒開機：先跑 `backfill --dry-run` 確認缺了哪些日期，再執行 `backfill`。`backfill` 不在每日 pipeline 內，只在手動指定時執行，可用參數有 `--start-date`、`--end-date`、`--prewarm-stock 2330`（可重複）。

---

## 5. 重啟與排錯

```bat
execution\restart_services.bat --docker --dry-run   :: 先預覽會做哪些事
execution\restart_services.bat --docker             :: 停掉本機的 app.py／MCP 程序，重啟 MCP + Web 容器，等兩個 /health 都回 200（最多約 20 分鐘，不重啟 DB）
```

| 狀況 | 處理 |
|---|---|
| DB 異常 | `docker compose restart db` → `docker compose ps` 等 db 變成 healthy → `execution\restart_services.bat --docker` |
| 1688／8080 被占用 | `netstat -ano \| findstr ":1688 :8080"`。如果是本 repo 的本機 Python 程序，用 `restart_services.bat --docker` 處理（會自動停掉）；如果是 `com.docker.backend` 或 `wslrelay`，代表容器正在使用，屬於正常狀況 |
| 晚間排程顯示「請檢查 MCP Server」 | 先確認 `http://127.0.0.1:8080/health`，再手動跑 `evening --stop-on-error`；已經過了當天就用 `backfill` |
| 容器一直停在 `health: starting` | 還在 `pip install`，看 `docker logs` 確認進度 |

重啟容器不需要改排程，只要避開 18:30 的晚間流程。

**本機模式（不用 Docker 的舊路徑）**：`execution\start_web.bat` 會用 `myenv` 直接啟動 `services\mcp\server.py` 和 `app.py`；也可以用 `execution\restart_services.bat`（不加 `--docker`）。本機模式和容器使用同一組埠，**兩者不要同時跑**。

---

## 6. 執行模式：Research 與 Production

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

- `PRODUCTION` 需要同時具備三項：`STOCK_LINBOT_MODE=production`、位於 repo 外且由 Owner 控制的可信授權檔、未過期的 `production_mode` grant。只設環境變數無效。設定方式見 [`docs/research/production_grant_setup.md`](docs/research/production_grant_setup.md)，驗證用 `python scripts/verify_production_grant.py`（唯讀）。
- `execution\evening_run.bat` 會設定 `STOCK_LINBOT_GRANTS`（授權檔位置）、`STOCK_LINBOT_MARKET_SCOPE=TWSE_ONLY`、`STOCK_LINBOT_TRIGGER=scheduled`。
- DB 寫入和 LINE 推播目前仍在 audit-only 階段，只有列進 `STOCK_LINBOT_ENFORCE` 的 operation 才會強制檢查。**Research 的外部 HTTP 一律強制**，不需要另外設定 enforcement 環境變數。
- 公開 Research 的固定預算：OD-P6 385、T1b 10、2026 日曆 1；以 SQLite 持久化，環境變數不能提高額度。沒有預算的 MOPS／Daily 會拒絕新請求，但既有快取仍可使用。見 [操作說明](docs/research/public_data_access.md)。
- 試驗管理：`scripts/research_workflow.py` 的 `admit_experiment` 會把每次已登記實驗寫入 `outputs/research_workflow/trial_ledger.jsonl`。相同實驗重跑記為 `REPLAY`（不限次數、不增加試驗數）；新設定記為 `TRIAL`（受登記的 `trial_budget` 限制，並在 DSR 試驗數 743 之上累計）。

---

## 7. 目錄與職責

| 路徑 | 職責 |
|---|---|
| `app/__init__.py` | 組合根與相容門面。`create_app()`、`from app import app`、`python app.py` 都可用 |
| `app/web_server.py` | Web 路由、JSON 契約、`/health` |
| `app/platform_routes.py`、`app/platform_v2_routes.py`、`app/intelligence_routes.py`、`app/research_payloads.py` | 研究平台、策略情報路由與研究頁面資料組裝 |
| `app/line_bot.py`、`line_flows.py`、`line_commands.py` | LINE webhook、postback、指令比對、Flex 訊息組裝 |
| `app/dashboard_payloads.py` | 健康檢查、總經、市場快照、MCP overlay 與本地 fallback |
| `app/news_overlay.py` | 新聞情緒資訊與降級處理 |
| `config.py`、`config/settings.py` | 所有設定與常數，從環境變數讀取 |
| `core/db_helper.py` | 共用資料庫存取；部分服務與 job 另有自己的 SQL |
| `core/mcp_client.py` | MCP 傳輸、重試、逾時 |
| `core/runtime/public_data.py` | 公開資料請求授權、限額與稽核邊界 |
| `core/runtime/operation_policy.py` | Research／Production 模式與操作閘門 |
| `core/strategy/` | `StrategyRegistry`（策略身分）、`StrategyRunner`（執行）、legacy adapter |
| `core/strategies/` | 各策略實作，包含舊版 `v31`–`v38` 模組 |
| `core/strategy_manager.py` | 設定、啟用策略與相容門面（`strategy_settings.json`） |
| `core/backtest/` | 回測引擎、成本、滑價、績效指標 |
| `core/runtime/` | Fundamental 策略的身分凍結、shadow、Fresh OOS 與上線閘門 |
| `core/research/`、`core/validation/` | 因子研究、PIT 資料、walk-forward 與穩健性驗證 |
| `core/crawlers/`、`core/update_*.py` | 融資融券、季財報、月營收補充資料 |
| `services/mcp/server.py` | MCP HTTP 服務（`scripts/twse_mcp_server.py` 是相容啟動器，容器使用它） |
| `services/market_data_update.py` | 市場資料更新、驗證與寫入流程 |
| `jobs/` | 正式 CLI 入口；研究類 `run_*` 只能明確指定執行 |
| `scripts/` | 研究執行、證據工具與部分執行期共用程式 |
| `execution/*.bat` | Windows 包裝：排程、手動選單、重啟、本機啟動 |
| `templates/`、`static/` | Web 前端（規範見 `openspec/specs/frontend-design.md`） |
| `docker-compose.yaml` | 正式服務；`docker-compose.research.yaml` 為研究用 |

---

## 8. 策略平台

- `StrategyRegistry` 是策略身分的唯一來源，負責解析 canonical ID 和舊版 alias。`StrategyRunner` 負責執行。`StrategyManager` 是設定與相容門面，不是另一套身分來源。
- 完整 canonical 策略清單以 `core/strategy_manager.py` 的 `CANONICAL_REGISTRY` 為準；啟用中的策略記錄在 `strategy_settings.json`。
- 舊策略 key（例如 `v34_turbo`、`v35_innovation`）透過 alias 解析。修改顯示名稱或 metadata 時，**不得改寫歷史資料庫紀錄**。
- Fundamental 策略（`fundamental_g2g3_top5_reb60_score_weighted_v1`）和 legacy 策略分開執行。它的身分已凍結，PIT／Fresh OOS 閘門由 `core/runtime/` 強制執行，未通過時一律封鎖。在 `ENABLE_FUNDAMENTAL_PRODUCTION` 開啟且閘門通過之前，它只會以 shadow 狀態顯示在 Strategy Center，不進入推薦和推播。細節見 [`docs/fundamental_daily_operations.md`](docs/fundamental_daily_operations.md)。
- 下單功能永遠關閉：

```text
BROKER_ORDER_SUBMISSION=DISABLED
```

服務不可用時，回應會明確標示 degraded／fallback 狀態；無法計算的績效以 `value=None` 加上原因表示，不用 `0` 或 infinity 代替。

---

## 9. 相容面（不得隨意移除）

`app.py`、`config.py`、`scripts/twse_mcp_server.py`、`execution/*.bat`、舊策略 alias，以及 CLI、Web、LINE 和 scheduler 的公開行為。

根目錄的 `4_run_backtest.py`、`5_push_to_line.py` 已於 2026-11-11 移除，請改用以下 canonical 指令（參數與舊策略旗標 `--v31`～`--v38` 不變）：

```powershell
python app.py
python jobs/run_backtest.py --strategies all --days 365 --mode balanced
python jobs/run_backtest.py --help
python jobs/push_to_line.py --help
python jobs/scheduler.py morning
```

---

## 10. 資料與產物

- `data/processed/`：不可變的研究證據與 manifest。
- `outputs/`：可重現的 job 輸出與 runtime 證據（包含 Fundamental shadow／OOS、排程 lock）。
- `artifacts/`：審查報告、回測產物與可續跑的來源快取。
- `docs/refactor/`：目前的架構、依賴、相容性與刪除決策。
- `openspec/`：規格（`specs/`）與變更（`changes/`，完成後移到 `archive/`）。只保留在本機，不進 git。

刪除非快取檔案前，必須先在 [`docs/refactor/deletion_candidates.md`](docs/refactor/deletion_candidates.md) 完成 `SAFE_TO_REMOVE` 紀錄。引用狀態不明時，一律保留（KEEP）。

---

## 11. 驗證

```powershell
python -m compileall -q app core jobs
python -c "import app; print(app.create_app())"
python -m pytest -q --timeout=10 test/test_daily_pipeline_consolidation_contract.py test/unit/runtime test/test_cli_dry_run.py
python -m pytest -q                       # 完整測試，約 4 分鐘
git diff --check
openspec validate <change-id> --strict --no-interactive
```

需要外部 MCP 或新聞服務的測試，請先啟動對應服務，或使用測試內建的 fixture。

延伸閱讀：[`docs/operations_runbook.md`](docs/operations_runbook.md)、[`docs/refactor/architecture-boundary-health_2026-10-06.md`](docs/refactor/architecture-boundary-health_2026-10-06.md)、[`docs/refactor/strategy-platform-runtime-contract-2026-10-06.md`](docs/refactor/strategy-platform-runtime-contract-2026-10-06.md)、[`docs/fundamental_daily_operations.md`](docs/fundamental_daily_operations.md)、[`docs/strategy_operations.md`](docs/strategy_operations.md)。
