# Implementation Plan: TWSE 數據流現代化

**Branch**: `001-modernize-twse-dataflow` | **Date**: 2026-04-01 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-modernize-twse-dataflow/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

以 async-first 的 `tool/mcp_client.py` 取代現有 HTML/`requests` 型爬蟲路徑，將個股基本資料、外資買賣超與歷史財報三個資料集統一收斂到受控的 MCP POST 契約；同時在 `docker-compose.yaml` 佈建 `stock_bot`、`twse_mcp_server` 與 `db` 的同網段健康檢查拓樸，並重構 `1_update_database.py`、`tool/update_financials_mops.py`、`tool/update_history_financials.py` 與 `tool/news_agent.py`，讓業務層只處理 orchestration、資料正規化與 LangChain tool 使用，不再直接碰觸外部 HTTP 傳輸。

## Technical Context

**Language/Version**: Python 3.11.9 runtime in `myenv` (repo convention remains Python 3.10+)  
**Primary Dependencies**: Flask, SQLAlchemy, pandas, line-bot-sdk v3, feedparser, Gemini SDK integration, legacy `requests`, planned `httpx`, planned LangChain tool/agent dependencies for `BaseTool` integration  
**Storage**: MySQL 8.0 via SQLAlchemy; preserve existing `daily_market_data`, `financial_statements`, `monthly_revenue`, and `daily_recommendations` contracts managed by `tool/db_helper.py`  
**Testing**: pytest unit/integration/contract-style tests plus `docker compose` health and orchestration smoke tests  
**Target Platform**: Windows development host and Linux containers orchestrated by `docker compose`; internal service-to-service traffic over one bridge network  
**Project Type**: Python Flask web app plus scheduled market-data pipeline plus ML-assisted recommendation service  
**Performance Goals**: P95 daily market snapshot + foreign flow sync within 120 seconds; single-quarter financial sync within 60 seconds; async MCP fan-out must reduce pre-`2_rundaily.py` network wait without changing downstream DB-driven semantics  
**Constraints**: No direct external HTTP in business scripts; no schema-breaking DB changes; explicit retries/logging only; health checks required for `db`, `stock_bot`, and `twse_mcp_server`; `tool/news_agent.py` remains compatible with current Gemini-driven summary path and `daily_recommendations` consumption  
**Scale/Scope**: Three MCP datasets, two financial update entry points, one daily market updater, one news-agent integration, and full TWSE/TPEx daily universe sized to thousands of securities per trade date

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Gate

- HTTP boundary: PASS. All new TWSE/TPEx/MOPS transport moves into `tool/mcp_client.py`; `1_update_database.py`, `tool/update_financials_mops.py`, `tool/update_history_financials.py`, and `tool/news_agent.py` become consumers of typed client methods rather than HTTP callers.
- Type safety: PASS. New MCP client, DTOs, orchestration helpers, and LangChain tool wrappers will carry full PEP 484 type hints; touched legacy functions will be annotated where signatures change.
- Error handling: PASS. Research locks in explicit transport/contract/upstream exception categories, bounded retries, structured logs, and partial-success semantics instead of bare `except:`.
- Dependency governance: PASS with tracked additions. `httpx` is the preferred transport client, and the LangChain dependency surface will be limited to the minimum required for `BaseTool` plus Gemini agent prompt integration; implementation must also reconcile the current Gemini package drift in `requirements.txt`.
- Documentation sync: PASS. Implementation must update `README.md`, `openspec/project.md`, affected module docstrings, this feature's `quickstart.md`, and the MCP contract file.

### Post-Design Re-check

- HTTP boundary: PASS. The design isolates all POST/health contracts behind one client module and keeps business orchestration code free of raw HTTP.
- Type safety: PASS. `research.md`, `data-model.md`, and the contract file define typed request/response shapes and the validation rules the implementation must satisfy.
- Error handling: PASS. The design uses retryable/non-retryable error distinctions and preserves successful module writes during partial failures.
- Dependency governance: PASS. The design records `httpx` and LangChain/Gemini alignment as explicit dependency work, not incidental drift.
- Documentation sync: PASS. All required planning and design artifacts are generated in this phase; implementation-facing docs to update are enumerated in `quickstart.md` and `research.md`.

## Project Structure

### Documentation (this feature)

```text
specs/001-modernize-twse-dataflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── twse-mcp-service.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
docker-compose.yaml
requirements.txt
config.py
app.py
1_update_database.py
2_rundaily.py

tool/
├── db_helper.py
├── mcp_client.py
├── news_agent.py
├── update_financials_mops.py
├── update_history_financials.py
└── crawlers/
    ├── chip_data_scraper.py          # legacy path to retire from covered scope
    └── quarterly_scraper.py          # legacy path to retire from covered scope

test/
├── test_mcp_client.py
├── test_update_database_mcp.py
├── test_financial_mcp_sync.py
└── test_news_agent_tools.py
```

**Structure Decision**: Keep the existing single-project Python layout. Add exactly one new transport boundary module under `tool/`, refactor existing orchestration scripts in place, and place new validation coverage under `test/` so downstream callers keep their current import and execution paths.

## Complexity Tracking

No constitution violations currently require exception tracking.
