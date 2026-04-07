# Implementation Plan: Rich Menu 數據驅動與 MCP 深度整合

**Feature Branch**: `002-richmenu-mcp-integration`
**Created**: 2026-04-02
**Status**: Ready for implementation

---

## Technical Context

### Existing Components

| Component | File | Role in this feature |
|-----------|------|---------------------|
| Rich Menu builder | `tool/richmenu.py` | Defines 2×2 area layout; `build_default_rich_menu_request()` to be updated |
| Postback router | `app.py: _build_postback_reply_messages()` | if/elif chain → dict-dispatch migration |
| Postback handler | `app.py: postback_handler()` | Entry point — no changes required |
| MCP transport | `tool/mcp_client.py: MCPClient` | Used by new handlers via sync façade methods |
| Strategy factory | `tool/strategy_manager.py: StrategyManager` | New `get_random_strategy_pool()` method |
| Settings file | `strategy_settings.json` | New optional key `random_strategy_pool` |
| Deploy helper | `scripts/setup_rich_menu.py` | **New file** — thin CLI wrapper |
| Integration tests | `test/test_richmenu_mcp_integration.py` | **New file** |

### Architecture Fit

```
LINE PostbackEvent
    ↓ app.py: postback_handler()
    ↓ _extract_postback_action(event.postback.data)  [existing — unchanged]
    ↓ _POSTBACK_HANDLERS[action]()                   [dict-dispatch — new pattern]
         ├─ market_summary  → _build_market_summary_messages()
         │       ↓ _postback_cache.get("market_summary:{date}")
         │       ↓ MCPClient.fetch_stock_basic_snapshot_sync()  [on cache miss]
         │       ↓ _aggregate_market_summary(payload)
         ├─ chip_trend      → _build_chip_trend_messages()
         │       ↓ _postback_cache.get("chip_trend:{date}")
         │       ↓ MCPClient.fetch_foreign_investor_flow_sync()  [on cache miss]
         │       ↓ _aggregate_chip_trend(payload)
         └─ random_strategy → _build_random_strategy_messages()
                 ↓ StrategyManager().get_random_strategy_pool()
                 ↓ random.sample(pool, len(pool))  → shuffle
                 ↓ for each key: strategy.filter_candidates(market_df)
                 ↓ return first non-empty result
```

### Module Dependencies to Add in `app.py`

```python
import random
import threading
import time
from zoneinfo import ZoneInfo
from tool.mcp_client import MCPClient, MCPClientError
```

These imports are all from the standard library or already-installed packages (`tool.mcp_client` already exists and `httpx` is in `requirements.txt`). No new packages required.

---

## Constitution Check

| Project Principle | Status |
|-------------------|--------|
| All DB ops through `tool.db_helper` | ✅ No new DB calls |
| All HTTP calls through `tool.mcp_client` | ✅ `MCPClient` used in handler functions |
| No raw SQL in application files | ✅ Not applicable |
| `from config import Config` pattern | ✅ Existing import; no new Config fields needed |
| No hardcoded strategy keys in app code | ✅ Pool from `strategy_settings.json` via `StrategyManager` |
| No build tools / npm | ✅ No frontend changes |
| Absolute imports only | ✅ All new imports are absolute |

---

## Implementation Phases

### Phase 1 — TTL Cache Class in `app.py`

**Scope**: Add `_PostbackCache` class and module-level `_postback_cache` singleton to `app.py`.

**Rationale for placement**: The cache is used only by postback handlers in `app.py`. There is no need for a separate module file — keeping it co-located avoids fragmenting a small, self-contained utility.

**Class contract** (`_PostbackCache`):

```python
class _PostbackCache:
    """Thread-safe in-memory TTL cache for postback upstream payloads."""

    _TTL_SECONDS: float = 3600.0

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def _today_taipei(self) -> str:
        return datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')

    def _make_key(self, action: str) -> str:
        return f"{action}:{self._today_taipei()}"

    def get(self, action: str) -> Any | None:
        key = self._make_key(action)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            payload, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return payload

    def set(self, action: str, payload: Any) -> None:
        """Store payload only if it is non-None and has non-empty records."""
        if payload is None:
            return
        records = payload.get('records') if isinstance(payload, dict) else None
        if isinstance(records, list) and len(records) == 0:
            return  # Do not cache empty upstream responses
        key = self._make_key(action)
        expires_at = time.monotonic() + self._TTL_SECONDS
        with self._lock:
            self._store[key] = (payload, expires_at)

# Module-level singleton
_postback_cache = _PostbackCache()
```

**Files changed**: `app.py`
**Tests**: `test/test_richmenu_mcp_integration.py` — `TestPostbackCache`

---

### Phase 2 — `_build_market_summary_messages()` in `app.py`

**Scope**: New handler function for `action=market_summary`.

**Pseudocode**:
```python
def _build_market_summary_messages() -> list:
    try:
        payload = _postback_cache.get('market_summary')
        if payload is None:
            client = MCPClient()
            trade_date = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
            payload = client.fetch_stock_basic_snapshot_sync(trade_date)
            _postback_cache.set('market_summary', payload)

        records = payload.get('records', [])
        if not records:
            return [V3TextMessage(text='📊 今日大盤無資料，可能為休市日。')]

        total_volume = sum(int(r.get('volume', 0)) for r in records)
        rising = sum(1 for r in records if float(r.get('close_price', 0)) > float(r.get('open_price', 0)))
        falling = sum(1 for r in records if float(r.get('close_price', 0)) < float(r.get('open_price', 0)))
        flat = len(records) - rising - falling
        trade_date = payload.get('as_of_date', '—')

        volume_str = f"{total_volume / 1e8:.1f} 億" if total_volume > 1e8 else f"{total_volume / 1e4:.0f} 萬"
        text = (
            f"📊 今日大盤概況（{trade_date}）\n\n"
            f"🔼 今日開收盤上漲：{rising} 檔\n"
            f"🔽 今日開收盤下跌：{falling} 檔\n"
            f"➡️ 平盤：{flat} 檔\n"
            f"💹 成交量合計：{volume_str}"
        )
        return [V3TextMessage(text=text)]

    except MCPClientError as exc:
        print(f"⚠️ market_summary MCP 失敗: {exc}")
        return [V3TextMessage(text='📊 大盤資料暫時無法取得，請稍後再試。')]
    except Exception as exc:
        print(f"⚠️ market_summary 處理失敗: {exc}")
        return [V3TextMessage(text='📊 大盤資料暫時無法取得，請稍後再試。')]
```

**Files changed**: `app.py`
**Tests**: `TestMarketSummaryHandler` — mock `MCPClient`, verify text fields, verify cache hit suppresses second call.

---

### Phase 3 — `_build_chip_trend_messages()` in `app.py`

**Scope**: New handler function for `action=chip_trend`.

**Pseudocode**:
```python
def _build_chip_trend_messages() -> list:
    try:
        payload = _postback_cache.get('chip_trend')
        if payload is None:
            client = MCPClient()
            trade_date = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d')
            payload = client.fetch_foreign_investor_flow_sync(trade_date)
            _postback_cache.set('chip_trend', payload)

        records = payload.get('records', [])
        if not records:
            return [V3TextMessage(text='💰 今日籌碼資料無內容，可能為休市日。')]

        def sum_field(field: str) -> int:
            return sum(int(r.get(field) or 0) for r in records)

        foreign_total = sum_field('foreign_buy')
        has_trust   = any(r.get('trust_buy')  is not None for r in records)
        has_dealer  = any(r.get('dealer_buy') is not None for r in records)

        def fmt(v: int) -> str:
            sign = '買超' if v >= 0 else '賣超'
            abs_v = abs(v)
            return f"{abs_v / 1e8:.2f} 億 ({sign})" if abs_v > 1e8 else f"{abs_v / 1e4:.0f} 萬 ({sign})"

        trade_date = payload.get('as_of_date', '—')
        lines = [
            f"💰 三大法人籌碼動向（{trade_date}）\n",
            f"🌏 外資：{fmt(foreign_total)}",
        ]
        if has_trust:
            lines.append(f"🏦 投信：{fmt(sum_field('trust_buy'))}")
        if has_dealer:
            lines.append(f"🏢 自營商：{fmt(sum_field('dealer_buy'))}")

        return [V3TextMessage(text='\n'.join(lines))]

    except MCPClientError as exc:
        print(f"⚠️ chip_trend MCP 失敗: {exc}")
        return [V3TextMessage(text='💰 籌碼資料暫時無法取得，請稍後再試。')]
    except Exception as exc:
        print(f"⚠️ chip_trend 處理失敗: {exc}")
        return [V3TextMessage(text='💰 籌碼資料暫時無法取得，請稍後再試。')]
```

**Files changed**: `app.py`
**Tests**: `TestChipTrendHandler` — mock `MCPClient`, test optional trust/dealer presence, cache behaviour.

---

### Phase 4 — `_build_random_strategy_messages()` in `app.py`

**Scope**: New handler function for `action=random_strategy`.

**Pseudocode**:
```python
def _build_random_strategy_messages() -> list:
    try:
        manager = StrategyManager()
        pool = manager.get_random_strategy_pool()
        if not pool:
            return [V3TextMessage(text='🎲 策略盲盒\n\n目前無已設定的策略，請檢查 strategy_settings.json。')]

        shuffled_pool = random.sample(pool, len(pool))  # full shuffle without replacement

        df, date_str = get_stock_data()
        if df.empty:
            return [V3TextMessage(text='🎲 策略盲盒\n\n今日市場資料尚未更新，請稍後再試。')]

        for strategy_key in shuffled_pool:
            strategy = manager.get_strategy(strategy_key)
            if strategy is None:
                continue
            try:
                candidates = strategy.filter_candidates(df.copy())
                if candidates is not None and not candidates.empty:
                    strategy_label = getattr(strategy, 'display_name', strategy_key)
                    header = f"🎲 策略盲盒 — 今日抽到：{strategy_label}\n\n"
                    # Re-use existing formatting helper for consistency
                    body = format_v31_recommendation(
                        candidates.head(5).to_dict('records'), date_str
                    )
                    return [V3TextMessage(text=header + body)]
            except Exception as exc:
                print(f"⚠️ 策略 {strategy_key} filter 失敗: {exc}")
                continue

        # All strategies exhausted with empty results
        strategy_label = getattr(manager.get_strategy(shuffled_pool[0]), 'display_name', shuffled_pool[0]) \
            if shuffled_pool else '—'
        return [V3TextMessage(
            text=f"🎲 策略盲盒 — {strategy_label}\n\n今日此策略無符合條件標的，明日再試。"
        )]

    except Exception as exc:
        print(f"⚠️ random_strategy 處理失敗: {exc}")
        return [V3TextMessage(text='🎲 策略盲盒執行失敗，請稍後再試。')]
```

**Files changed**: `app.py`
**Tests**: `TestRandomStrategyHandler` — mock `StrategyManager.get_random_strategy_pool()`, mock `filter_candidates`, verify fallback chain, verify empty pool message.

---

### Phase 5 — Dict-Dispatch Router Migration in `app.py`

**Scope**: Replace `_build_postback_reply_messages()` if/elif with `_POSTBACK_HANDLERS` dict.

**Before**:
```python
def _build_postback_reply_messages(action: str) -> list:
    if action == 'get_macro_news':
        return _build_macro_news_messages()
    if action == 'get_journal':
        return [V3TextMessage(text=_build_journal_reflection_text())]
    return [V3TextMessage(text='⚠️ 尚未支援的 Rich Menu 指令')]
```

**After**:
```python
# Module-level dispatch map — add new handlers here without touching router logic
_POSTBACK_HANDLERS: dict[str, Callable[[], list]] = {}

def _register_postback_handlers() -> dict[str, Callable[[], list]]:
    return {
        'get_macro_news':   _build_macro_news_messages,
        'get_journal':      lambda: [V3TextMessage(text=_build_journal_reflection_text())],
        'market_summary':   _build_market_summary_messages,
        'chip_trend':       _build_chip_trend_messages,
        'random_strategy':  _build_random_strategy_messages,
    }

# Populated after all handler functions are defined
_POSTBACK_HANDLERS = _register_postback_handlers()


def _build_postback_reply_messages(action: str) -> list:
    """Resolve LINE rich-menu postback actions into reply messages."""
    handler = _POSTBACK_HANDLERS.get(action)
    if handler is None:
        return [V3TextMessage(text='⚠️ 尚未支援的 Rich Menu 指令')]
    return handler()
```

**Ordering constraint**: `_register_postback_handlers()` must be called **after** all handler functions are defined. Place it immediately after `_build_random_strategy_messages()`.

**Files changed**: `app.py`
**Tests**: `TestPostbackRouter` — verify each registered key routes correctly; verify unknown key returns fallback; verify existing `get_macro_news` and `get_journal` still work.

---

### Phase 6 — `StrategyManager.get_random_strategy_pool()` in `tool/strategy_manager.py`

**Scope**: Add one new method to the existing `StrategyManager` class.

**Default pool** (if `random_strategy_pool` key absent from settings):
`['v35_innovation', 'v36_chip_momentum', 'v38_value_dividend']`

```python
_DEFAULT_RANDOM_POOL: list[str] = [
    'v35_innovation',
    'v36_chip_momentum',
    'v38_value_dividend',
]

def get_random_strategy_pool(self) -> list[str]:
    """Return the validated strategy pool for the random blind-box action.

    Reads ``random_strategy_pool`` from strategy_settings.json, then
    intersects with STRATEGY_REGISTRY to guarantee all keys are loadable.

    Returns:
        List of valid strategy keys in the configured pool order.
        Falls back to _DEFAULT_RANDOM_POOL if key is absent.
    """
    settings = self.get_settings()
    pool = settings.get('random_strategy_pool', _DEFAULT_RANDOM_POOL)
    if not isinstance(pool, list):
        pool = _DEFAULT_RANDOM_POOL
    validated = [k for k in pool if k in self.STRATEGY_REGISTRY]
    if len(validated) < len(pool):
        skipped = set(pool) - set(validated)
        print(f"⚠️ random_strategy_pool 中有無效策略鍵，已略過: {skipped}")
    return validated
```

**Files changed**: `tool/strategy_manager.py`
**Tests**: `TestStrategyManagerPool` — verify default, custom, partial-invalid, and empty pool scenarios.

---

### Phase 7 — Rich Menu Layout Update in `tool/richmenu.py`

**Scope**: Update `build_default_rich_menu_request()` to the new 4-button mapping.

**Change**: Replace all four `RichMenuArea` entries:

| Area | Old Action | New Action |
|------|-----------|-----------|
| Top-left (0,0) | `MessageAction(label="推薦", text="推薦")` | `MessageAction(label="個股診斷", text="診斷 ")` |
| Top-right (1250,0) | `PostbackAction(label="總經摘要", data="action=get_macro_news", ...)` | `PostbackAction(label="總經與大盤", data="action=market_summary", ...)` |
| Bottom-left (0,843) | `PostbackAction(label="日誌反思", data="action=get_journal", ...)` | `PostbackAction(label="籌碼動向", data="action=chip_trend", ...)` |
| Bottom-right (1250,843) | `MessageAction(label="持股", text="持股")` | `PostbackAction(label="策略盲盒", data="action=random_strategy", ...)` |

> **Note on existing `get_macro_news` / `get_journal`**: These actions are still handled by the dispatch map. Only the Rich Menu button labels change; the underlying handlers remain registered and reachable from any client still sending the old postback data strings.

**Files changed**: `tool/richmenu.py`
**Tests**: `TestRichMenuLayout` — assert all four areas have correct action types and data/text strings.

---

### Phase 8 — New Deploy Script `scripts/setup_rich_menu.py`

**Scope**: Minimal CLI script.

```python
#!/usr/bin/env python
"""Deploy the default Rich Menu to LINE by calling the canonical builder."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool.richmenu import sync_default_rich_menu_from_token


def main() -> None:
    rich_menu_id = sync_default_rich_menu_from_token()
    print(f"[OK] Rich Menu 部署完成: {rich_menu_id}")


if __name__ == '__main__':
    main()
```

**Files changed**: `scripts/setup_rich_menu.py` (new file)
**Tests**: No unit test (pure I/O script); covered by manual smoke test in `quickstart.md`.

---

### Phase 9 — Test Suite `test/test_richmenu_mcp_integration.py`

**Test classes**:

| Class | What it tests |
|-------|--------------|
| `TestPostbackCache` | TTL expiry, cache miss, cache hit, empty-payload guard, cross-date key isolation, thread-safety (basic) |
| `TestMarketSummaryHandler` | Normal reply format, cache hit (MCPClient called once), empty records, MCPClientError → friendly message |
| `TestChipTrendHandler` | With/without trust/dealer fields, cache hit, MCPClientError graceful |
| `TestRandomStrategyHandler` | Pool shuffled, first non-empty wins, all-empty fallback, empty pool, strategy engine exception |
| `TestPostbackRouter` | Each of 5 keys routes to correct handler; unknown key → fallback; regression `get_macro_news`/`get_journal` |
| `TestStrategyManagerPool` | Default pool, custom pool from settings, invalid keys filtered, empty pool after filter |
| `TestRichMenuLayout` | All 4 areas — action type, label, data/text string |

**Mocking strategy**:
- `MCPClient.fetch_stock_basic_snapshot_sync` and `fetch_foreign_investor_flow_sync` are patched via `unittest.mock.patch`.
- `StrategyManager.get_random_strategy_pool` and `get_strategy` are patched for isolation.
- `get_stock_data` in `app` module is patched for `random_strategy` tests with a minimal DataFrame.

---

## Dependency-Ordered Task Summary

```
Phase 1 (Cache)          — no deps
Phase 6 (StrategyManager) — no deps
Phase 7 (RichMenu layout) — no deps
Phase 8 (setup script)   — no deps (just calls phase 7 indirectly)
Phase 2 (market_summary) — deps: Phase 1 (cache)
Phase 3 (chip_trend)     — deps: Phase 1 (cache)
Phase 4 (random_strategy)— deps: Phase 6 (StrategyManager pool method)
Phase 5 (Dispatch Map)   — deps: Phases 2, 3, 4 (all handlers defined first)
Phase 9 (Tests)          — deps: all phases complete
```

**Recommended implementation order**: 1 → 6 → 7 → 8 → 2 → 3 → 4 → 5 → 9

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `stock_basic_snapshot` returns 0 records on holiday | High | Medium | Empty-records guard returns friendly "休市" message, does not cache |
| LINE delivers duplicate PostbackEvent within cache TTL | Medium | Low | Cache makes second execution idempotent |
| `MCPClient` sync façade raises inside async event loop (Flask dev server single-thread) | Low | High | `_run_sync()` raises `MCPConfigurationError` if running loop detected; Flask prod runs in thread pool (no loop), so safe |
| `random.sample(pool, len(pool))` when pool has 1 element | Low | None | `random.sample` with k=len works for single-element lists |
| `ZoneInfo('Asia/Taipei')` not available on some systems | Low | Medium | `from zoneinfo import ZoneInfo` is stdlib since Python 3.9; fallback: `import pytz` if needed — document in requirements.txt comment |

---

## Constitution Check (post-design)

| Area | Status |
|------|--------|
| All DB ops through `tool.db_helper` | ✅ No new DB calls; `get_stock_data` already routes through db_helper |
| All external HTTP through `tool.mcp_client` | ✅ Both new handlers use `MCPClient` exclusively |
| Singleton Settings via `StrategyManager` | ✅ `get_random_strategy_pool()` reads from same settings |
| No hardcoded strategy keys | ✅ Pool from JSON; defaults only as module-level constant in StrategyManager |
| Existing `get_macro_news` / `get_journal` untouched | ✅ Migrated into dispatch map with zero behaviour change |
| Thread safety | ✅ `threading.Lock` in `_PostbackCache` |
| New imports follow absolute import rule | ✅ All new imports are absolute |
| No new packages required | ✅ `random`, `threading`, `time`, `zoneinfo` are stdlib; `MCPClient` already in codebase |
