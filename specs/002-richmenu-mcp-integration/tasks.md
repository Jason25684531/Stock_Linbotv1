# Implementation Tasks: Rich Menu 數據驅動與 MCP 深度整合

**Feature**: Rich Menu with MCP Integration & Smart Cache  
**Feature Branch**: `002-richmenu-mcp-integration`  
**Created**: 2026-04-02  
**Status**: Ready for implementation

---

## Overview

**3 User Stories** (from spec.md):
- **US1 (P1)**: Market Summary — 大盤快照一鍵取得 (`market_summary` action)
- **US2 (P2)**: Chip Trend — 三大法人籌碼動向 (`chip_trend` action)
- **US3 (P3)**: Strategy Blind Box — 隨機策略盲盒 (`random_strategy` action)

**Tech Stack** (per plan.md):
- Python 3.10+, Flask, LINE Bot SDK v3, MCPClient (sync façade)
- Cache: `threading.Lock` + `time.monotonic()` TTL (1 hour)
- Dispatch: dict-based router (`_POSTBACK_HANDLERS`)

**Success Criteria** (per spec.md):
- SC-001: 100% of 4 Rich Menu buttons trigger correct Actions
- SC-002: 95% of cold requests < 5s; 95% of warm cache < 1s
- SC-003: Same action in 1h window = 1 upstream call (cache hit)
- SC-004: `random_strategy` shows 2+ different strategies across 5 triggers
- SC-005: All 3 actions handle errors gracefully (no traceback to LINE)
- SC-006: Existing `get_macro_news` / `get_journal` still work

---

## Phase 0: Foundation (No User Story Dependencies)

### T001 — TTL Cache Class in `app.py`

- [ ] T001 Create `_PostbackCache` class with threading.Lock and TTL expiry in app.py
  - File: `app.py` (new class ~50 lines)
  - Implement: `get(action)`, `set(action, payload)`, `_today_taipei()`, `_make_key(action)`
  - Validate: payload must not be None; records list must be non-empty before cache write
  - Test in: `test/test_richmenu_mcp_integration.py: TestPostbackCache`
  - Accept: Cache hit suppresses second call; empty payloads never cached; TTL expiry works

### T002 — StrategyManager Pool Method in `tool/strategy_manager.py`

- [ ] T002 Add `get_random_strategy_pool()` method to StrategyManager class
  - File: `tool/strategy_manager.py` (new method ~20 lines)
  - Read: `strategy_settings.json["random_strategy_pool"]` with fallback default
  - Validate: intersect with `STRATEGY_REGISTRY.keys()` to filter invalid entries
  - Log: warn and skip invalid strategy keys
  - Test in: `test/test_richmenu_mcp_integration.py: TestStrategyManagerPool`
  - Accept: Default pool returned if key absent; invalid keys silently filtered; empty pool after filter is valid

### T003 — Rich Menu Layout Update in `tool/richmenu.py`

- [ ] T003 Update `build_default_rich_menu_request()` with 4-button mapping
  - File: `tool/richmenu.py` (existing function, 4 areas)
  - Area 0,0 (top-left): MessageAction(text="診斷 ") [change from "推薦"]
  - Area 1250,0 (top-right): PostbackAction(data="action=market_summary") [change from get_macro_news]
  - Area 0,843 (bottom-left): PostbackAction(data="action=chip_trend") [change from get_journal]
  - Area 1250,843 (bottom-right): PostbackAction(data="action=random_strategy") [change from MessageAction "持股"]
  - Test in: `test/test_richmenu_mcp_integration.py: TestRichMenuLayout`
  - Accept: All 4 area bounds & action data strings match spec; existing image upload loop unchanged

### T004 — Deploy Script `scripts/setup_rich_menu.py`

- [ ] T004 Create `scripts/setup_rich_menu.py` thin deployment wrapper
  - File: `scripts/setup_rich_menu.py` (new file, ~20 lines)
  - Import: `from tool.richmenu import sync_default_rich_menu_from_token`
  - Call: `sync_default_rich_menu_from_token()` and print result
  - Require: `LINE_CHANNEL_ACCESS_TOKEN` in environment
  - Manual smoke test: `python scripts/setup_rich_menu.py` returns menu ID and prints "[OK]"

---

## Phase 1: Market Summary Handler (User Story 1 — P1)

### T005 — Market Summary MCP Fetch in `app.py`

- [ ] T005 Implement `_build_market_summary_messages()` handler function
  - File: `app.py` (new function ~60 lines after Phase 0 cache class)
  - Logic:
    1. Check `_postback_cache.get('market_summary')` → cache hit short-path
    2. Else: `MCPClient().fetch_stock_basic_snapshot_sync(trade_date)` with today's date
    3. Store payload in cache via `_postback_cache.set('market_summary', payload)`
    4. Extract: total_volume, rising_count, falling_count, flat_count from records
    5. Format: "📊 今日大盤概況" message with volume formatted as 億/萬
    6. Return: `[V3TextMessage(text=...)]`
  - Error handling: MCPClientError → "📊 大盤資料暫時無法取得，請稍後再試。"
  - Empty records: "📊 今日大盤無資料，可能為休市日。"
  - Test in: `test/test_richmenu_mcp_integration.py: TestMarketSummaryHandler`
  - Accept: Text includes all 4 metrics; cache hit verified by mocking MCPClient call count; cache TTL validated

### T006 — Market Summary Postback Registration

- [ ] T006 Register `market_summary` handler in postback dispatch map
  - File: `app.py` (in `_register_postback_handlers()` function)
  - Add: `'market_summary': _build_market_summary_messages` entry
  - Verify: `_build_postback_reply_messages('market_summary')` routes correctly
  - Test: Existing tests for postback_handler continue to pass

---

## Phase 2: Chip Trend Handler (User Story 2 — P2)

### T007 — Chip Trend MCP Fetch in `app.py`

- [ ] T007 Implement `_build_chip_trend_messages()` handler function
  - File: `app.py` (new function ~60 lines)
  - Logic:
    1. Check `_postback_cache.get('chip_trend')` → cache hit
    2. Else: `MCPClient().fetch_foreign_investor_flow_sync(trade_date)`
    3. Store in cache via `_postback_cache.set('chip_trend', payload)`
    4. Aggregate: sum(foreign_buy), sum(trust_buy), sum(dealer_buy) from records
    5. Format: "💰 三大法人籌碼動向" with each fund type formatted as 億元/萬元
    6. Include optional trust/dealer only if present in source data
    7. Return: `[V3TextMessage(text=...)]`
  - Error handling: MCPClientError → "💰 籌碼資料暫時無法取得，請稍後再試。"
  - Empty records: "💰 今日籌碼資料無內容，可能為休市日。"
  - Test in: `test/test_richmenu_mcp_integration.py: TestChipTrendHandler`
  - Accept: Format handles 2-fund (foreign only) and 3-fund (with trust/dealer) cases; cache works

### T008 — Chip Trend Postback Registration

- [ ] T008 Register `chip_trend` handler in postback dispatch map
  - File: `app.py` (in `_register_postback_handlers()`)
  - Add: `'chip_trend': _build_chip_trend_messages` entry
  - Test: Route correctly via `_build_postback_reply_messages('chip_trend')`

---

## Phase 3: Random Strategy Handler (User Story 3 — P3)

### T009 — Random Strategy Selection Logic in `app.py`

- [ ] T009 Implement `_build_random_strategy_messages()` handler function
  - File: `app.py` (new function ~70 lines)
  - Logic:
    1. Get pool: `StrategyManager().get_random_strategy_pool()`
    2. Check empty: if pool empty → return "🎲 策略盲盒\n\n目前無已設定的策略"
    3. Get market data: `get_stock_data()` → df
    4. Shuffle pool: `random.sample(pool, len(pool))`
    5. Fallback loop: try each strategy in shuffled order
       - `manager.get_strategy(key)` → load strategy object
       - `strategy.filter_candidates(df.copy())` → get candidates
       - If non-empty: format and return with strategy label
    6. If all empty: return "🎲 ...無符合條件標的"
  - Error handling:
    - Strategy load error → log, skip to next strategy
    - Market data missing → return "🎲 今日市場資料尚未更新"
    - Exception during filter → log, skip to next strategy
  - Test in: `test/test_richmenu_mcp_integration.py: TestRandomStrategyHandler`
  - Accept: Shuffled pool verified by multiple trigger calls; all-empty case handled; strategy exceptions don't crash

### T010 — Random Strategy Postback Registration

- [ ] T010 Register `random_strategy` handler in dispatch map
  - File: `app.py` (in `_register_postback_handlers()`)
  - Add: `'random_strategy': _build_random_strategy_messages` entry
  - No caching for this action (always fresh)

---

## Phase 4: Postback Router Refactoring

### T011 — Dict-Dispatch Map Migration in `app.py`

- [ ] T011 Replace `_build_postback_reply_messages()` if/elif chain with dict dispatch
  - File: `app.py` (refactor existing function ~5 lines → dict lookup)
  - Create module-level dict: `_POSTBACK_HANDLERS: dict[str, Callable[[], list]]`
  - Create registration function: `_register_postback_handlers()` (returns dict with 5 keys)
  - Migrate existing handlers:
    - `'get_macro_news': _build_macro_news_messages`
    - `'get_journal': lambda: [V3TextMessage(text=_build_journal_reflection_text())]`
  - New handlers (from T005, T007, T009):
    - `'market_summary': _build_market_summary_messages`
    - `'chip_trend': _build_chip_trend_messages`
    - `'random_strategy': _build_random_strategy_messages`
  - Refactor router: `_build_postback_reply_messages(action)` becomes:
    ```python
    handler = _POSTBACK_HANDLERS.get(action)
    return handler() if handler else [V3TextMessage(text='⚠️ 尚未支援的 Rich Menu 指令')]
    ```
  - Placement: `_register_postback_handlers()` called **after** all handler functions are defined
  - Test in: `test/test_richmenu_mcp_integration.py: TestPostbackRouter`
  - Accept: Each of 5 keys routes to correct handler; unknown key returns fallback; backwards compat with get_macro_news/get_journal

---

## Phase 5: Integration & Cross-Cutting Concerns

### T012 — Update Strategy Settings JSON

- [ ] T012 Add `random_strategy_pool` key to `strategy_settings.json`
  - File: `strategy_settings.json`
  - New key:
    ```json
    "random_strategy_pool": ["v35_innovation", "v36_chip_momentum", "v38_value_dividend"]
    ```
  - Manual: Edit file or auto-populate on first `StrategyManager().get_random_strategy_pool()` call
  - Validation: StrategyManager logs each key that passes REGISTRY check

### T013 — Import Statements in `app.py`

- [ ] T013 Add required imports to `app.py` for new handlers
  - File: `app.py` (top-level imports section)
  - Add (if not present):
    - `import random`
    - `import threading`
    - `import time`
    - `from zoneinfo import ZoneInfo`
    - `from datetime import datetime`
    - `from tool.mcp_client import MCPClient, MCPClientError`
  - Verify no circular imports after adding these

### T014 — Update AGENTS.md with New Architecture

- [ ] T014 Document Rich Menu architecture in `openspec/AGENTS.md`
  - File: `openspec/AGENTS.md` (already partially updated)
  - Sections:
    - Dict-dispatch pattern explanation
    - How to add new `action` keys
    - Random pool configuration via `strategy_settings.json`
    - Cache ownership and TTL details
  - Already completed in planning phase

---

## Phase 6: Comprehensive Test Suite

### T015 — Unit Tests for Postback Cache

- [X] T015 Implement `TestPostbackCache` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~100 lines)
  - Test cases:
    1. `test_cache_miss_returns_none()` — before first set
    2. `test_cache_set_and_get()` — store and retrieve payload
    3. `test_cache_empty_payload_not_stored()` — None payload rejected
    4. `test_cache_empty_records_not_stored()` — records=[] not cached
    5. `test_cache_ttl_expiry()` — mock time.monotonic to verify expiry
    6. `test_cache_date_boundary()` — different dates have separate cache keys
    7. `test_cache_thread_safety_basic()` — concurrent get/set don't corrupt store
  - Mock: `time.monotonic`, `datetime.now(ZoneInfo(...))`
  - Assert: payload round-trip, TTL behavior, key isolation

### T016 — Unit Tests for Market Summary Handler

- [X] T016 Implement `TestMarketSummaryHandler` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~80 lines)
  - Test cases:
    1. `test_cache_hit_no_mcp_call()` — pre-load cache, verify MCPClient not called
    2. `test_cold_start_mcp_call()` — empty cache, verify MCPClient.fetch_stock_basic_snapshot_sync called
    3. `test_response_format_contains_all_metrics()` — message contains volume, rising, falling, flat
    4. `test_volume_formatting_billion()` — large volume → 億 format
    5. `test_mcp_error_returns_friendly_message()` — MCPClientError → "暫時無法取得"
    6. `test_empty_records_returns_holiday_message()` — records=[] → "可能為休市日"
  - Mock: `MCPClient.fetch_stock_basic_snapshot_sync`, `_postback_cache`
  - Assert: cache called first; message text contains key fields

### T017 — Unit Tests for Chip Trend Handler

- [X] T017 Implement `TestChipTrendHandler` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~80 lines)
  - Test cases:
    1. `test_cache_hit_suppresses_call()` — cache behavior as market_summary
    2. `test_foreign_only_response()` — all 3 institutions present
    3. `test_missing_trust_dealer_fields()` — only foreign_buy in records
    4. `test_fund_total_aggregation()` — sum of per-stock buys
    5. `test_sign_formatting_buy_vs_sell()` — positive=買超, negative=賣超
    6. `test_magnitude_formatting_wan_vs_yi()` — format as 萬/億 based on value
    7. `test_mcp_error_friendly_message()` — MCPClientError handling
  - Mock: `MCPClient.fetch_foreign_investor_flow_sync`, `_postback_cache`

### T018 — Unit Tests for Random Strategy Handler

- [X] T018 Implement `TestRandomStrategyHandler` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~120 lines)
  - Test cases:
    1. `test_empty_pool_returns_no_strategy_message()` — get_random_strategy_pool() returns []
    2. `test_pool_shuffle_shows_variety()` — call handler 5 times, verify 2+ different strategies
    3. `test_first_nonempty_strategy_wins()` — fallback loop: skip empty, return first non-empty
    4. `test_all_strategies_empty_fallback_message()` — shuffled pool all return no candidates
    5. `test_missing_market_data_returns_update_message()` — get_stock_data() returns empty df
    6. `test_strategy_load_error_skips_to_next()` — get_strategy() raises, loop continues
    7. `test_filter_candidates_exception_logged_skipped()` — filter raises, no candidates, next strategy tried
    8. `test_response_includes_strategy_label()` — returned message has strategy name
  - Mock: `StrategyManager.get_random_strategy_pool()`, `StrategyManager.get_strategy()`, `get_stock_data()`
  - Assert: strategy diversity over multiple calls; error resilience; proper fallback

### T019 — Integration Tests for Postback Router

- [X] T019 Implement `TestPostbackRouter` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~60 lines)
  - Test cases:
    1. `test_market_summary_action_routes_correctly()` — action key → right handler
    2. `test_chip_trend_action_routes_correctly()` — same
    3. `test_random_strategy_action_routes_correctly()` — same
    4. `test_get_macro_news_backward_compat()` — existing action still works
    5. `test_get_journal_backward_compat()` — existing action still works
    6. `test_unknown_action_returns_unsupported_message()` — action=foo → fallback message
  - Mock: All handler functions to return distinct messages
  - Assert: `_build_postback_reply_messages(action)` returns correct message type

### T020 — Integration Tests for StrategyManager Pool

- [X] T020 Implement `TestStrategyManagerPool` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~50 lines)
  - Test cases:
    1. `test_default_pool_when_key_absent()` — strategy_settings.json missing key
    2. `test_custom_pool_from_settings()` — custom pool loaded and returned
    3. `test_invalid_keys_filtered_out()` — unregistered strategies skipped with warning
    4. `test_empty_pool_after_filter()` — custom pool all invalid → empty list returned
    5. `test_non_list_pool_value_uses_default()` — pool value is wrong type → default
  - Mock: `StrategyManager.get_settings()`, `StrategyManager.STRATEGY_REGISTRY`
  - Assert: pool validation logic

### T021 — Unit Tests for Rich Menu Layout

- [X] T021 Implement `TestRichMenuLayout` in test file
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~50 lines)
  - Test cases:
    1. `test_area_0_MessageAction_diagnostic()` — top-left is MessageAction with text="診斷 "
    2. `test_area_1_PostbackAction_market_summary()` — top-right postback data="action=market_summary"
    3. `test_area_2_PostbackAction_chip_trend()` — bottom-left postback data="action=chip_trend"
    4. `test_area_3_PostbackAction_random_strategy()` — bottom-right postback data="action=random_strategy"
    5. `test_all_bounds_correct()` — verify pixel coordinates (0,0), (1250,0), (0,843), (1250,843)
  - Call: `build_default_rich_menu_request()` and inspect returned object
  - Assert: area count, action types, data/text strings, bounds

### T022 — End-to-End Postback Flow Test

- [X] T022 Implement `TestPostbackFlow` regression test
  - File: `test/test_richmenu_mcp_integration.py` (new test class ~40 lines)
  - Test cases:
    1. `test_postback_event_extracts_action_correctly()` — _extract_postback_action("action=market_summary") → "market_summary"
    2. `test_postback_handler_routes_to_correct_reply()` — mock PostbackEvent, verify reply_message called with right messages
    3. `test_backward_compat_old_postback_data()` — old action=get_macro_news still works
  - Setup: Mock ApiClient, MessagingApi, PostbackEvent
  - Assert: postback_handler routes to _build_postback_reply_messages which returns list of Messages

---

## Phase 7: Documentation & Validation

### T023 — Update README with New Features

- [X] T023 Document new Rich Menu features in `README.md`
  - File: `README.md` (section on Rich Menu and Postback Actions)
  - Sections:
    - Rich Menu 2×2 layout overview
    - 3 new actions: market_summary, chip_trend, random_strategy
    - Cache behavior and TTL (1 hour)
    - Strategy pool configuration
    - How to deploy: `python scripts/setup_rich_menu.py`

### T024 — Update Docstrings for New Functions

- [X] T024 Add PEP 484 type hints and docstrings to all new functions
  - Files: `app.py` (4 new handler functions, 1 new class), `tool/strategy_manager.py` (1 new method)
  - Format: Standard docstring with Args, Returns, Raises sections
  - Type hints: All parameters and return types specified
  - Example: See plan.md for pseudocode
  - Verify: `mypy app.py` (if type checker enabled) passes

### T025 — Run All Tests

- [X] T025 Execute full test suite to verify no regressions
  - Command: `pytest test/test_richmenu_mcp_integration.py -v`
  - Command: `pytest test/ -k "postback or richmenu" -v` (include regression tests)
  - Coverage: Aim for ≥ 90% line coverage in new code
  - Pass criteria: All tests green; no deprecation warnings; no new exceptions

### T026 — Manual Smoke Test

- [ ] T026 Deploy to local LINE account and test all 4 button flows
  - Setup: Ensure `MCP_BASE_URL=http://localhost:8080` in `.env`
  - Setup: Ensure MCP server running: `python scripts/twse_mcp_server.py`
  - Action: Click each button in Rich Menu:
    1. 個股診斷 → verify diagnostic message appears
    2. 總經與大盤 → verify market summary with volume & metrics
    3. 籌碼動向 → verify chip trend with fund totals
    4. 策略盲盒 → verify strategy label & candidates
  - Repeat: Call same action twice within 1 hour → second reply should be instant (cache hit)
  - Error case: Kill MCP server → verify all 3 cached actions return friendly "暫時無法取得" message
  - Result: Screenshot or live video of all flows

---

## Phase 8: Deployment & Release

### T027 — Update CHANGELOG

- [ ] T027 Document feature in `CHANGELOG.md` or git commit message
  - Entry:
    - Date: 2026-04-02
    - Version: v0.2.0 or next planned release
    - Features: 3 new Postback actions, TTL cache, dict-dispatch router
    - Files: app.py, tool/richmenu.py, tool/strategy_manager.py, strategy_settings.json, scripts/setup_rich_menu.py, tests

### T028 — Git Commit & Push

- [ ] T028 Create clean git commit with all changes
  - Branch: `002-richmenu-mcp-integration`
  - Commit message:
    ```
    feat: Rich Menu MCP integration with market summary, chip trend, and strategy blind box
    
    - Add TTL cache for market data (1h) to reduce upstream API load
    - Implement dict-dispatch router for Postback actions (extensible pattern)
    - New actions: market_summary, chip_trend, random_strategy
    - Update Rich Menu to 4-button layout
    - Add comprehensive test suite (TestPostbackCache, TestMarketSummaryHandler, ...)
    - Add deploy script: scripts/setup_rich_menu.py
    
    Fixes: #XXX  (if applicable)
    ```
  - Run: `git status` → verify only intended files; `git diff` → review changes
  - Push: `git push origin 002-richmenu-mcp-integration`

### T029 — Create Pull Request

- [ ] T029 Open PR on GitHub with feature branch
  - Title: "feat: Rich Menu MCP Integration"
  - Description: Link to spec.md, plan.md; reference Success Criteria (SC-001 to SC-006)
  - Checklist:
    - [ ] All tests pass locally
    - [ ] No new linting errors
    - [ ] Docstrings and type hints complete
    - [ ] Manual smoke test passed
    - [ ] CHANGELOG updated
  - Reviewers: Assign project maintainers

---

## Dependencies & Parallel Execution

### Dependency Graph

```
T001 (Cache)                    ✓ No deps
T002 (StrategyManager method)   ✓ No deps
T003 (Rich Menu layout)         ✓ No deps
T004 (Deploy script)            → T003
T005 (Market handler)           → T001
T006 (Market registration)      → T005
T007 (Chip handler)             → T001
T008 (Chip registration)        → T007
T009 (Random handler)           → T002
T010 (Random registration)      → T009
T011 (Dispatch router)          → T005, T007, T009, T006, T008, T010
T012 (Update settings JSON)     → T002
T013 (Imports in app.py)        → T005, T007, T009
T014 (Update AGENTS.md)         ✓ Already done
T015–T022 (Tests)               → all implementation tasks
T023–T024 (Docs)                → all implementation tasks
T025 (Run tests)                → T015–T022
T026 (Manual smoke)             → all implementation + T004
T027–T029 (Release)             → T025, T026
```

### Recommended Execution Order (Critical Path)

**Sequential phases** (some parallelizable within each phase):

1. **T001, T002, T003, T004** (Foundation — can run in parallel)
2. **T005, T007, T009** (Handlers — can run in parallel)
3. **T006, T008, T010** (Registration — depends on 2, can run in parallel)
4. **T011** (Router — depends on 3)
5. **T012, T013** (Settings + Imports — depend on 1, 2, 5)
6. **T014** (Update AGENTS — already done)
7. **T015–T022** (Tests — depend on all impl, parallelizable within test class)
8. **T023–T025** (Docs + run tests)
9. **T026** (Manual smoke)
10. **T027–T029** (Release)

**Fast track** (for experienced dev): 1 → 2 → 3 → 4 → 13 → 15–22 → 25 → 26 → 28–29 (skip T012, T014, T023–T024, T027)

---

## Success Metrics (Per Spec.md)

| SC # | Success Criterion | Task Coverage |
|------|-------------------|----------------|
| SC-001 | 100% of 4 buttons trigger correct Actions | T003, T021, T022, T026 |
| SC-002 | 95% cold < 5s, 95% warm < 1s | T005, T007, T015, T016, T017, T026 |
| SC-003 | Same action within 1h = 1 upstream call | T001, T015, T016, T017 |
| SC-004 | 2+ strategies across 5 triggers | T009, T018, T026 |
| SC-005 | All 3 new actions handle errors gracefully | T005, T007, T009, T016, T017, T018 |
| SC-006 | Existing `get_macro_news`/`get_journal` work | T011, T019, T022 |

---

## Quality Checklist

Before marking T025 (Run All Tests) complete, verify:

- [ ] All new code has type hints (PEP 484)
- [ ] All new functions have docstrings (Args, Returns, Raises)
- [ ] No bare `except:` statements (per project constitution)
- [ ] All error paths return friendly messages (not tracebacks)
- [ ] Cache invalidation logic correct (no stale cross-day data)
- [ ] No circular imports introduced
- [ ] Import statements all absolute (per constitution)
- [ ] Existing handlers (`get_macro_news`, `get_journal`) unchanged
- [ ] `tool.mcp_client.MCPClient` used exclusively for HTTP calls
- [ ] `tool.db_helper` used exclusively for DB calls (none in this feature)
