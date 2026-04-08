# Quickstart: TWSE 數據流現代化

This document describes the implemented verification flow for the MCP-backed TWSE data pipeline.

## Prerequisites

- Docker and `docker compose` are available locally.
- The project `.env` provides production-facing secrets, while compose injects container-safe service URLs such as `DB_URL` and `MCP_BASE_URL`.
- `twse_mcp_server` exposes a HTTP health endpoint and the dataset POST endpoints defined in `contracts/twse-mcp-service.openapi.yaml`.
- `stock_bot` exposes a lightweight HTTP health endpoint from the Flask process.

## 1. Start the composed stack

```bash
docker compose up --build -d db twse_mcp_server stock_bot
docker compose ps
```

Expected result:
- `db`, `twse_mcp_server`, and `stock_bot` all transition to `healthy`.
- `stock_bot` starts only after `db` and `twse_mcp_server` are healthy.

## 2. Inspect health and startup logs

```bash
docker compose logs --tail=50 twse_mcp_server
docker compose logs --tail=50 stock_bot
```

Expected result:
- MCP service reports healthy startup and route registration.
- `stock_bot` reports successful configuration load and no connection failures to `db` or `twse_mcp_server`.

## 3. Run the MCP-backed daily market sync

```bash
docker compose exec stock_bot python 1_update_database.py
```

Expected result:
- Market snapshot and foreign investor flow are fetched through `tool/mcp_client.py`.
- Financial update steps use the MCP-backed path instead of the legacy quarterly scraper.
- Logs show retry counts, module durations, and a final summary without raw scraper warnings for covered datasets.

## 4. Smoke-test the quarter update path

```bash
docker compose exec stock_bot python tool/update_financials_mops.py --year 114 --quarter 4 --dry-run
docker compose exec stock_bot python tool/update_history_financials.py --start-year 114 --end-year 114 --delay 1
```

Expected result:
- Single-quarter and historical backfill utilities both read financial data through the MCP client boundary.
- No `QuarterlyScraper` HTML parsing path is required for the covered financial flow.

## 5. Verify downstream compatibility

```bash
docker compose exec stock_bot python 2_rundaily.py
docker compose exec stock_bot python -c "from tool.news_agent import get_news_sector_boost; import json; print(json.dumps(get_news_sector_boost(), ensure_ascii=False))"
```

Expected result:
- `2_rundaily.py` still reads from the existing DB tables without transport changes.
- News-agent output is still valid JSON/structured data after the LangChain tool boundary is introduced.

## 6. Run the focused smoke commands

```bash
docker compose exec stock_bot python -c "from tool.mcp_client import MCPClient; print(callable(MCPClient.fetch_many_sync))"
docker compose exec stock_bot python -c "from tool.news_agent import build_mcp_prompt_context; print(build_mcp_prompt_context())"
docker compose exec stock_bot python -c "from config import Config; print(Config.MCP_BASE_URL)"
```

Expected result:
- The MCP client exposes the parallel fetch helper.
- `tool/news_agent.py` can build MCP-backed prompt context without changing its public API.
- The bot container resolves the same `MCP_BASE_URL` as the updater scripts.

## 7. Host-mode fallback (optional)

If container orchestration is not available, point the bot process at a reachable MCP base URL and run the same Python entry points from the project root.

```powershell
$env:MCP_BASE_URL="http://localhost:8080"
$env:DB_URL="mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"
python scripts/twse_mcp_server.py
python 1_update_database.py
python tool/update_financials_mops.py --year 113 --quarter 4 --dry-run
python 2_rundaily.py
```