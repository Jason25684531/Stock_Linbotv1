# Research: TWSE 數據流現代化

## Decision 1: Preserve the current MySQL/SQLAlchemy storage contract and treat the spec's "SQLite schema" wording as a logical schema reference

- Decision: Implementation will preserve the existing `tool/db_helper.py` storage contract backed by MySQL 8.0 and SQLAlchemy. The feature will use the current table shapes in `daily_market_data`, `financial_statements`, `monthly_revenue`, and `daily_recommendations` as the authoritative persistence interface.
- Rationale: The repository runtime is MySQL-first (`Config.SQLALCHEMY_DATABASE_URI` defaults to `mysql+pymysql://...`), and downstream consumers such as `2_rundaily.py`, `app.py`, and backtest flows already depend on those tables. Reinterpreting the prompt's SQLite wording as "existing logical schema" avoids a disruptive persistence migration.
- Alternatives considered: Create a parallel SQLite persistence path. Rejected because it would add a second storage contract, break current deployment assumptions, and expand scope beyond MCP modernization.

## Decision 2: Add an explicit three-service compose topology with one shared bridge network and health checks

- Decision: `docker-compose.yaml` will define `db`, `stock_bot`, and `twse_mcp_server` on a shared bridge network, with health checks on all three services and `depends_on.condition: service_healthy` for the bot.
- Rationale: The current compose file only defines MySQL, has no health checks, and relies on the default Docker network. The requested environment-layer outcome needs deterministic service discovery and safe startup ordering for MCP traffic.
- Alternatives considered: Keep the default bridge network and use startup sleeps instead of health checks. Rejected because it weakens observability and causes fragile race conditions during container startup.

## Decision 3: Implement `tool/mcp_client.py` as an async-first `httpx` client with a synchronous facade for orchestration scripts

- Decision: `tool/mcp_client.py` will own all MCP POST calls, use `httpx.AsyncClient` internally for concurrent dataset fetches, and expose a thin synchronous facade so existing CLI scripts can adopt it incrementally.
- Rationale: The constitution explicitly prefers `httpx` for new async I/O, and the feature requires non-blocking multi-dataset fetches. A sync facade keeps `1_update_database.py` and finance update entry points simple while preserving future reuse by async contexts.
- Alternatives considered: Continue with `requests`, or move `asyncio` and raw HTTP details directly into `1_update_database.py`. Rejected because both violate the new transport-boundary rule and would keep orchestration code coupled to protocol concerns.

## Decision 4: Treat `1_update_database.py` as orchestration-only and keep `2_rundaily.py` DB-driven

- Decision: `1_update_database.py` will become the main orchestrator that injects `MCPClient`, concurrently fetches market snapshot plus foreign flow, merges those datasets, and triggers MCP-backed financial updates. `2_rundaily.py` will stay DB-driven and benefit indirectly from a faster upstream pipeline.
- Rationale: The current `2_rundaily.py` does not fetch the covered external datasets directly; it reads from `daily_market_data` and `financial_statements`. The right optimization target is the stage that blocks `2_rundaily.py`, not a new HTTP path inside the strategy engine.
- Alternatives considered: Move MCP calls into `2_rundaily.py`. Rejected because it would bypass the architecture boundary and duplicate ingestion logic across two workflows.

## Decision 5: Modernize both daily and historical financial update paths, not only `1_update_database.py`

- Decision: `tool/update_financials_mops.py` and `tool/update_history_financials.py` must be migrated to the MCP-backed path together with the daily updater.
- Rationale: Historical financial statements are explicitly in scope. Leaving those utilities on `QuarterlyScraper` would preserve a second legacy HTML route and produce inconsistent data quality between ad hoc backfills and daily runs.
- Alternatives considered: Refactor only `run_financial_update()` inside `1_update_database.py`. Rejected because it would leave the standalone quarter update and historical backfill tools on the deprecated scraper path.

## Decision 6: Introduce LangChain only for the requested tool/agent boundary, not as a full rewrite of the news pipeline

- Decision: `tool/news_agent.py` will wrap selected `MCPClient` operations as LangChain `BaseTool` instances and update the agent prompt around those tools, while preserving the current synchronous Gemini summarization path as the outer control flow.
- Rationale: The user explicitly requested `BaseTool` wrapping and prompt updates, but the current news pipeline is entirely synchronous and already tied to Gemini output formatting. A focused tool boundary satisfies the requirement without forcing an unnecessary full-agent rewrite across all news logic.
- Alternatives considered: Keep direct Gemini calls only, or rewrite all of `tool/news_agent.py` around a full async agent loop. Rejected because the former ignores the requested LangChain layer and the latter adds avoidable runtime risk.

## Decision 7: Normalize dependency drift as part of the same feature

- Decision: The implementation will align runtime and declared dependencies in the same change: add `httpx`, add the minimum LangChain/Gemini integration packages needed for `BaseTool` + agent prompt execution, and reconcile the current Gemini package mismatch in `requirements.txt`.
- Rationale: The code imports `from google import genai`, but `requirements.txt` currently declares `google-generativeai==0.8.6` rather than the package family implied by the runtime import. This drift must be corrected before containerized builds and health-checked startup become reliable.
- Alternatives considered: Leave dependency cleanup for a later change. Rejected because the feature depends on container builds and new transport/tooling packages, so unresolved dependency drift would make the implementation brittle from the start.

## Decision 8: Use HTTP health endpoints as the canonical readiness checks

- Decision: `stock_bot` will expose a lightweight HTTP health endpoint in the Flask app, and `twse_mcp_server` will expose its own `/health` endpoint for compose readiness checks.
- Rationale: The repository already runs a long-lived Flask process on port 1688, but it has no health route today. HTTP health checks make compose startup order explicit and provide a reusable smoke-test surface for quickstart and operations.
- Alternatives considered: Use shell command probes only. Rejected because they validate process existence but not service readiness or dependency connectivity.