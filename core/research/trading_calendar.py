"""One entry for trading-day status (change 2026-11-09-autonomous-data-readiness-v1, design D4).

Rules are scripts/research_workflow.session_status (exchange data wins once cached; otherwise the imported schedule and stated closures).
Only the queried date's MI_INDEX file is opened. A weekday of a year without an imported schedule is CALENDAR_UNVERIFIED, never open.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

OPEN = ('OPEN_OBSERVED', 'EXPECTED_OPEN', 'CALENDAR_UNVERIFIED')  # not known closed


def status(day, folders=None, closures=None) -> str:
    """OPEN_OBSERVED, CLOSED_OBSERVED, CLOSED_SCHEDULED, CONFLICT, EXPECTED_OPEN or CALENDAR_UNVERIFIED.
    folders: MI_INDEX cache folders to look in, first hit wins (default: the research calendar cache)."""
    from core.research.sources import RawResponse
    from core.research.sources.twse import ResponseKind, classify
    from scripts import research_workflow as rw

    day = pd.Timestamp(day)
    days = {}
    for folder in (folders if folders is not None else (rw.MI_INDEX,)):
        path = Path(folder) / f'MI_INDEX_{day:%Y%m%d}.json'
        if path.is_file():
            payload = json.loads(path.read_text(encoding='utf-8'))
            kind = classify(RawResponse('twse', 'MI_INDEX', {}, datetime.now(timezone.utc), None, payload, None)).kind
            if kind not in (ResponseKind.TRADING_DAY, ResponseKind.NON_TRADING_DAY):
                continue  # an error, empty or out-of-range reply observes nothing: never a closure (decision.md D5)
            days[day] = (kind == ResponseKind.TRADING_DAY, path, payload)
            break
    scheduled, years = rw.load_closures(closures or rw.CLOSURES)
    result = rw.session_status(day, days, scheduled, years)
    return 'CALENDAR_UNVERIFIED' if result == 'EXPECTED_OPEN_UNVERIFIED' else result


def update_closures() -> set:
    """Closures observed by the market data service (an official no-data MI_INDEX reply for a past date, decision.md D5)."""
    import os
    root = Path(os.environ.get('STOCK_LINBOT_UPDATE_ROOT') or Path(__file__).resolve().parents[2] / 'outputs/market_data_updates')
    try:
        return set(json.loads((root / 'calendar' / 'closures.json').read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return set()


def publish_freshness(market_date, recommendation_date, today: date, time_slot: str, status_fn=None):
    """(ok, expected session, reason). Evening expects the latest open day up to today, morning the latest before today. Unverified
    weekdays count as open, so data that has not arrived blocks the push instead of sending older picks (design D10); a push also needs
    the expected session itself verified (imported annual schedule or exchange data, Owner brief 2026-11-09)."""
    if status_fn is None:
        observed = update_closures()
        status_fn = lambda day: 'CLOSED_OBSERVED' if day.isoformat() in observed else status(day)
    anchor = today if time_slot == 'evening' else today - timedelta(days=1)
    conflict = next((anchor - timedelta(days=k) for k in range(15) if status_fn(anchor - timedelta(days=k)) in OPEN + ('CONFLICT',)), None)
    if conflict is not None and status_fn(conflict) == 'CONFLICT':  # exchange data and schedule disagree: stop, never pick the day before
        return False, conflict.isoformat(), f'EXPECTED_SESSION:{conflict.isoformat()} (CONFLICT); market={market_date}; recommendations={recommendation_date}'
    expected, state = expected_trade_date(anchor, status_fn=status_fn)
    expected = expected.isoformat() if expected else None
    ok = expected is not None and state != 'CALENDAR_UNVERIFIED' and str(market_date)[:10] == expected \
        and str(recommendation_date)[:10] == expected
    reason = '' if ok else f'EXPECTED_SESSION:{expected} ({state}); market={market_date}; recommendations={recommendation_date}'
    return ok, expected, reason


def expected_trade_date(today: date, folders=None, closures=None, status_fn=None):
    """(latest date <= today not known to be closed, its status); (None, 'CALENDAR_UNVERIFIED') if none within 15 days.
    CONFLICT days are skipped. status_fn(day) replaces the file lookup (the update service passes its own folders)."""
    for offset in range(15):
        day = today - timedelta(days=offset)
        found = status_fn(day) if status_fn else status(day, folders, closures)
        if found in OPEN:
            return day, found
    return None, 'CALENDAR_UNVERIFIED'
