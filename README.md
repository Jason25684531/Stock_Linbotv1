# Stock AI Line Bot

Stock AI Line Bot is a Taiwan-market research and recommendation system with
four supported runtime surfaces: Web dashboard, LINE Bot, command-line jobs,
and the central scheduler. Runtime behavior is Python 3.11 in Docker Compose;
use the same version for local development.

## Quick start

Create `.env` from `.env.example` and provide credentials through environment
variables. At minimum, the Compose stack needs `MYSQL_ROOT_PASSWORD`,
`MYSQL_PASSWORD`, `ADMIN_PASSWORD`, and `FLASK_SECRET_KEY`. Optional runtime
settings include `LINE_TOKEN`, `LINE_SECRET`, `GEMINI_KEY`, `GEMINI_MODEL`,
`DB_URL`, and `MCP_BASE_URL`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Start the database, MCP service, and Web/LINE service.
docker compose up --build -d db twse_mcp_server stock_bot
docker compose ps
```

The application listens on `http://localhost:1688`; the MCP service listens on
`http://localhost:8080`. Health endpoints are `/health` on both services.
`requirements.runtime.txt` is the clean-checkout manifest for `stock_bot` and
`requirements.mcp.txt` is the clean-checkout manifest for `twse_mcp_server`.
The broad `requirements.txt` file is the development/research environment, not
the service contract.

## Runtime ownership and data flow

```text
official TWSE/MOPS data
        -> core/research normalization, PIT adjustment, factors, selection
        -> daily recommendation artifacts and database snapshots
        -> Web/LINE presentation and optional backtest/report outputs
```

- `app/__init__.py` is the application composition root and compatibility
  facade. `create_app()` returns the initialized Flask application while the
  historical `from app import app` and `python app.py` paths remain supported.
- `app/web_server.py` owns Web routes and JSON contracts.
- `app/line_bot.py`, `app/line_flows.py`, and `app/line_commands.py` own LINE
  webhook handling, postbacks, command matching, and Flex message assembly.
- `app/dashboard_payloads.py` owns health, macro, war-room, serialization,
  MCP-overlay, and local-fallback payload behavior. It remains the compatibility
  facade for callers and tests.
- `app/news_overlay.py` owns news sentiment enrichment and bounded fallback
  behavior.
- `jobs/scheduler.py` is the only scheduler owner. The normal daily pipeline is
  `update_database -> fundamental shadow operation -> run_daily -> lightweight
  backtest validation -> push_to_line`.
- `core/db_helper.py` owns persistence helpers; `core/mcp_client.py` owns MCP
  transport; `services/mcp/server.py` owns the MCP HTTP service.

The supported legacy-compatible daily command flow is:

`jobs/update_database.py` -> `jobs/run_daily.py` -> `jobs/run_daily_backtest_validation.py` -> `jobs/push_to_line.py`

Recommendation and price provenance fields are retained end to end:
`price_trade_date`, `price_source_date`, `price_basis`, `price_data_source`,
`price_is_stale`, `recommendation_close_price`, `recommendation_trade_date`,
`recommendation_price_basis`, `recommendation_is_stale`, `trade_date`,
`source_date`, `data_source`, and `is_stale`.

## Strategy Platform

`StrategyRegistry` is the canonical strategy identity authority and resolves
canonical IDs and legacy aliases. `StrategyRunner` is the execution dispatcher.
`StrategyManager` remains the settings, active-strategy, and compatibility
facade; it is not a parallel identity authority. Existing legacy strategy keys,
database values, and alias modules are retained for compatibility.

The Fundamental runtime is isolated from legacy strategy execution. Its frozen
identity, PIT/OOS gates, and fail-closed eligibility are enforced by
`core/runtime/` and `jobs/run_daily.py`. It is shadow/advisory until its gates
pass, and broker submission remains disabled:

```text
BROKER_ORDER_SUBMISSION=DISABLED
```

Do not rewrite historical strategy keys or database records when changing
presentation names or registry metadata.

## Supported entrypoints

```powershell
# Web and LINE compatibility launcher
python app.py

# Canonical jobs
python jobs/update_database.py
python jobs/run_daily.py
python jobs/push_to_line.py --time evening
python jobs/run_backtest.py --strategies all --days 365 --mode balanced

# Central scheduler (preferred for scheduled operations)
python jobs/scheduler.py daily
python jobs/scheduler.py daily --dry-run

# Historical wrappers; retained until separately approved removal evidence
python 4_run_backtest.py --help
python 5_push_to_line.py --help
```

`app.py`, `config.py`, `4_run_backtest.py`, `5_push_to_line.py`, old strategy
aliases, and scheduler behavior are compatibility surfaces. They must not be
removed as part of routine cleanup.

## Data and artifact retention

- `data/processed/` contains immutable research evidence and manifests.
- `outputs/` contains reproducible job output and runtime evidence.
- `artifacts/` contains review reports and resumable source caches.
- `docs/refactor/` contains current architecture, dependency, compatibility,
  and deletion decisions; historical OpenSpec archives remain historical.

Non-cache source or artifact deletion requires a complete `SAFE_TO_REMOVE`
record in [`docs/refactor/deletion_candidates.md`](docs/refactor/deletion_candidates.md).
Unknown dependency or reference status means KEEP.

## 操作說明

正式排程請使用 `jobs/scheduler.py`；不要直接建立第二套 scheduler。若
MCP、資料庫或新聞服務不可用，系統應回傳可診斷的 degraded/fallback 狀態，
不可用 `0` 或虛假的績效值掩蓋資料缺失。任何刪除都必須先完成
`docs/refactor/deletion_candidates.md` 的多面向引用證據。

## Verification

Compile and import checks:

```powershell
python -m compileall -q app core jobs
python -c "import app; print(app.create_app())"
python -c "import app.web_server, app.line_bot, app.line_flows, app.dashboard_payloads, app.news_overlay; print('imports_ok')"
```

Focused contract checks:

```powershell
python -m pytest -q --timeout=10 test/unit/strategy/test_strategy_runner.py test/unit/backtest/test_runner_selection.py test/unit/runtime/test_fundamental_production_integration.py
python -m pytest -q --timeout=10 test/test_cli_dry_run.py test/characterization/test_legacy_cli.py test/characterization/test_scheduler_baseline.py
python -m pytest -q --timeout=10 test/test_dashboard_health_check_api.py
```

The full suite is `python -m pytest -q`. Tests that call external MCP/news
services may require those services or should be run with their documented
fixtures. Before submitting a change, also run:

```powershell
git diff --check
openspec validate change-2026-10-06-architecture-simplification-readme-alignment --strict
```

For architecture details and historical decisions, see
[`docs/refactor/architecture-boundary-health_2026-10-06.md`](docs/refactor/architecture-boundary-health_2026-10-06.md),
[`docs/refactor/strategy-platform-runtime-contract-2026-10-06.md`](docs/refactor/strategy-platform-runtime-contract-2026-10-06.md),
and the archived material under [`openspec/changes/archive`](openspec/changes/archive).
