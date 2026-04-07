# Research: Rich Menu 數據驅動與 MCP 深度整合

**Feature Branch**: `002-richmenu-mcp-integration`
**Date**: 2026-04-02
**Status**: Complete — all NEEDS CLARIFICATION resolved

---

## Decision 1: `TWSEMCPClient` vs `MCPClient`

**Decision**: Use the existing `MCPClient` class directly — no alias or wrapper class required.

**Rationale**: The spec user description referred to `TWSEMCPClient`, but inspection of `tool/mcp_client.py` reveals the only public transport class is `MCPClient` (exposed in `__all__`). Creating an alias `TWSEMCPClient = MCPClient` would add a public name with no behaviour difference. The implementation will import `MCPClient` from `tool.mcp_client` directly in `app.py` handler functions.

**Alternatives considered**: Subclass `MCPClient` as `TWSEMCPClient` with helper methods for market summary and chip trend aggregation. Rejected: adds a new class file and inheritance overhead for what are stateless aggregation steps better handled as module-level functions in `app.py`.

---

## Decision 2: Market Summary Data Source & Aggregation

**Decision**: Derive market summary from `stock_basic_snapshot` by aggregating per-stock records.

- `total_volume` = sum of `records[].volume`
- `rising_count` = count of records where `close_price > open_price`
- `falling_count` = count of records where `close_price < open_price`

The reply message labels this as "今日開盤 → 收盤漲跌概況" to accurately represent the derivation (open-to-close, not prev-close-to-close).

**Rationale**: The existing `stock_basic_snapshot` MCP endpoint (and the `twse_mcp_server.py` implementation) provides `open_price` and `close_price` per stock. There is no `prev_close` or `change` field in the existing contract. Adding a dedicated `/v1/market-overview` endpoint would require modifying `scripts/twse_mcp_server.py`, which is outside this feature's stated file scope (`tool/richmenu.py`, `app.py`, `scripts/setup_rich_menu.py`). The open-to-close proxy is a meaningful same-day signal clearly labelled to users.

**Alternatives considered**:
1. New `/v1/market-overview` endpoint on MCP server — accurate 漲跌家數 but out of scope, requires server changes.
2. Database query against `stock_data` for previous day close — bypasses the dispatch "all external calls via MCPClient" constraint and adds DB latency.

---

## Decision 3: Chip Trend Aggregation Method

**Decision**: Aggregate `foreign_investor_flow` records into market-level totals.

- `foreign_total` = sum of `records[].foreign_buy` (signed, positive=net buy)
- `trust_total` = sum of `records[].trust_buy` (optional, 0 if absent)
- `dealer_total` = sum of `records[].dealer_buy` (optional, 0 if absent)

Display format uses `億元` or `萬張` depending on magnitude.

**Rationale**: The `foreign_investor_flow` endpoint already returns per-stock three-institution data. Simple summation yields market-level totals. No additional endpoint needed.

**Alternatives considered**: Per-stock top-10 ranking (買超最多個股). Rejected for this feature — too much detail for a single LINE message; better as a separate future feature slice.

---

## Decision 4: TTL Cache Implementation

**Decision**: Module-level `_PostbackCache` class using `threading.Lock` + `dict[str, tuple[Any, float]]` where the float is a `time.monotonic()` expiry timestamp. No new package dependencies.

**Cache key format**: `f"{action}:{date_str}"` where `date_str` is `datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')`.

**Cross-day invalidation**: Because the key includes the date string, a Monday request with key `market_summary:2026-03-30` never collides with `market_summary:2026-04-01`. Keys simply miss on a new trade day, triggering a fresh fetch.

**Empty payload guard**: `set()` validates `payload is not None` and `len(records) > 0` before storing, so empty upstream responses are never cached.

**Rationale**: `threading.Lock` provides mutual exclusion without external dependencies. `time.monotonic()` avoids system-clock skew during TTL expiry checks. For the single-process Flask server assumed by spec Assumption 5, this is sufficient. A future scale-out to multiple workers would require Redis, but that is explicitly out of scope.

**Alternatives considered**: `functools.lru_cache` — lacks per-key TTL and trading-date awareness. `cachetools.TTLCache` — would introduce a new dependency (`cachetools`) for minimal gain over a ten-line custom class.

---

## Decision 5: Postback Dispatch Pattern (Dict-Mapping)

**Decision**: Replace the current `_build_postback_reply_messages()` if/elif chain with a `dict[str, Callable[[], list]]` dispatch map registered at module load time. Existing handlers (`get_macro_news`, `get_journal`) are migrated into the map as lambdas/references with zero behavioural change.

```python
_POSTBACK_HANDLERS: dict[str, Callable[[], list]] = {
    'get_macro_news':   _build_macro_news_messages,
    'get_journal':      lambda: [V3TextMessage(text=_build_journal_reflection_text())],
    'market_summary':   _build_market_summary_messages,
    'chip_trend':       _build_chip_trend_messages,
    'random_strategy':  _build_random_strategy_messages,
}
```

`_build_postback_reply_messages(action)` becomes a one-liner:
```python
return _POSTBACK_HANDLERS.get(action, _build_unknown_action_messages)(action)
```

**Rationale**: The spec constraint explicitly requires a dispatch map (映射表) that allows adding new action keys without touching routing logic. The dict lookup is O(1) and the function signature contract is uniform (`() -> list[Message]`). The user request also specifically asks for "Postback 路由分流 (Switch-Case 或 Dict Mapping 模式)".

**Alternatives considered**: Class-based command pattern — unnecessary abstraction for a flat action space of 5 handlers.

---

## Decision 6: Random Strategy Pool Configuration

**Decision**: Add a new optional key `random_strategy_pool: list[str]` to `strategy_settings.json`, defaulting to `['v35_innovation', 'v36_chip_momentum', 'v38_value_dividend']`. `StrategyManager.get_random_strategy_pool()` reads this key and intersects it with `STRATEGY_REGISTRY` to guarantee only loadable strategies are in the pool.

**Rationale**: The spec requires dynamic pool configuration without code changes (FR-011). Storing it in `strategy_settings.json` follows the existing singleton settings pattern for this project. Intersection with `STRATEGY_REGISTRY` prevents errors from typos or stale pool entries.

**Alternatives considered**: Hardcode in `app.py` — violates FR-011. Use `active_strategies` as pool — active strategies rotate and may not represent the desired blind-box variety. Create a separate JSON file — unnecessary fragmentation.

---

## Decision 7: `scripts/setup_rich_menu.py` Scope

**Decision**: A minimal CLI wrapper that performs: `from tool.richmenu import sync_default_rich_menu_from_token; sync_default_rich_menu_from_token()`. Prints outcome. Requires `LINE_CHANNEL_ACCESS_TOKEN` in environment.

**Rationale**: The spec notes this file should delegate to `tool/richmenu.py` with no logic duplication. It is a deployment convenience script, not a source-of-truth for the menu definition.

---

## Constitution Check (pre-design)

| Area | Status |
|------|--------|
| All external data calls through `tool/mcp_client.py` | ✅ MCPClient used exclusively |
| DB operations through `tool/db_helper.py` | ✅ No new DB calls introduced |
| No hardcoded strategy keys in application code | ✅ Pool from settings JSON |
| No build tools; Flask + Jinja2 only | ✅ No frontend changes |
| No duplicate HTTP client logic in app files | ✅ `_build_*_messages` helpers call MCPClient |
| New imports in `app.py` follow absolute import rules | ✅ `from tool.mcp_client import MCPClient` |
