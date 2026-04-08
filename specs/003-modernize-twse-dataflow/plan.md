# Implementation Plan: TWSE MCP 現代化資料流與動態互動升級

**Branch**: `003-modernize-twse-dataflow` | **Date**: 2026-04-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-modernize-twse-dataflow/spec.md`

## Summary

本次實作不是從零開始，而是在既有 MCP modernization 基礎上補齊與使用者要求完全對齊的相容層。核心工作分為三塊：一是在 `tool/mcp_client.py` 中提供 `TWSEMCPClient` 與 `/v1/tools/*` 風格端點封裝，同時把欄位轉型維持在 client 端；二是將 `1_update_database.py`、`tool/update_financials_mops.py`、`app.py`、`scripts/setup_rich_menu.py`、`tool/news_agent.py` 對齊新的 client API，但不改動既有資料表與寫入契約；三是新增 `test/test_mcp_integration.py`，明確驗證 HTTP 500 發生時 client 會安全回傳 `None`，不把例外往上炸穿呼叫端。

## Technical Context

**Language/Version**: Python 3.11.9 in `myenv` (project convention remains Python 3.10+)  
**Primary Dependencies**: Flask, pandas, SQLAlchemy, line-bot-sdk v3, httpx, langchain-core, pydantic  
**Storage**: MySQL via `tool.db_helper.py`; keep `daily_market_data` and `financial_statements` contracts unchanged  
**Testing**: pytest, VS Code Problems/Pylance syntax checks, targeted MCP client mock tests  
**Target Platform**: Windows development host, local venv, optional Docker compose deployment  
**Project Type**: Python Flask app plus scheduled market-data pipeline plus Line Bot integration  
**Constraints**: No DB schema changes for this feature; all data mapping lives in `tool/mcp_client.py`; syntax/type validation after every Python file edit; commits at major milestones only; do not revert unrelated changes  
**Scope**: Focus on compatibility completion for MCP transport, data pipeline orchestration, Rich Menu postback path, and news agent tools

## Constitution Check

- DB boundary: PASS. Existing writes remain in `tool.db_helper.upsert_stock_data()` and `tool.db_helper.upsert_financial_statements()`.
- HTTP boundary: PASS after implementation. All covered external calls remain behind `tool/mcp_client.py`.
- Type safety: PASS with required follow-up. All newly added or changed public methods will keep explicit type hints.
- Error handling: PASS with planned refinement. Client compatibility methods must convert retry-exhausted HTTP failures into `None` for caller-safe paths.
- Documentation sync: PASS with planned update. This feature requires README-level note plus plan/tasks tracking.

## Project Structure

### Documentation (this feature)

```text
specs/003-modernize-twse-dataflow/
├── spec.md
├── plan.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
1_update_database.py
app.py
scripts/setup_rich_menu.py

tool/
├── db_helper.py
├── mcp_client.py
├── news_agent.py
├── richmenu.py
├── update_financials_mops.py
└── update_history_financials.py

test/
├── test_richmenu_mcp_integration.py
└── test_mcp_integration.py
```

**Structure Decision**: Keep the current single-project layout. Add the requested compatibility surface in `tool/mcp_client.py`, then consume it incrementally from existing orchestration modules so current imports and runtime paths stay stable.

## Implementation Strategy

### Phase 1: Compatibility Client First

1. Add `TWSEMCPClient` in `tool/mcp_client.py` without breaking existing `MCPClient` call sites.
2. Normalize `/v1/tools/*` responses into the same payload shape already used by DB writers.
3. Add safe methods that return `None` on HTTP 500 / transport failure for interactive callers and tests.

### Phase 2: Controlled Consumer Refactor

1. Update `1_update_database.py` and `tool/update_financials_mops.py` to consume the new compatibility client.
2. Preserve all DB write signatures and upsert contracts.
3. Keep Rich Menu action strings as `market_summary`, `chip_trend`, and `random_strategy`.

### Phase 3: Verification and Hardening

1. Add `test/test_mcp_integration.py` for HTTP 500 -> `None` behavior.
2. Run syntax/type checks after each Python edit.
3. Run focused pytest validation on new and touched integration tests.

## Complexity Tracking

- Existing code already implements most of the feature under different naming and stricter exception semantics.
- Main risk is introducing regressions while aligning to the new endpoint names and safe-return behavior.
- Mitigation is to preserve existing `MCPClient` APIs and add compatibility methods rather than replacing the current transport contract outright.