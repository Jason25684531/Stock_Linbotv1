"""Monthly return table for V2 runs (change 2026-11-12, design D3 analysis).

Same base as core.research.intelligence.performance._yearly: each month runs from the previous month's last close (the track's first
row for the first month), so a year's months compound exactly to its yearly return. A month without sessions is None with reason
NO_SESSIONS, never 0.0. holding_sessions = 0 marks a month spent in cash (its 0.0% is real, shown as NO_TRADES).
"""
from __future__ import annotations

import pandas as pd


def monthly_table(nav: pd.DataFrame, ledger: pd.DataFrame | None = None) -> dict:
    nav = nav.assign(date=pd.to_datetime(nav.date)).reset_index(drop=True)
    held = None
    if ledger is not None and len(ledger):
        held = ledger.assign(date=pd.to_datetime(ledger.date)).set_index('date').holdings
    out, prev = {}, nav.iloc[0] if len(nav) else None
    for year in sorted(set(nav.date.dt.year)):
        months, chain = {}, 1.0
        for month in range(1, 13):
            group = nav[(nav.date.dt.year == year) & (nav.date.dt.month == month)]
            group = group[group.index > 0]  # the base row is the previous close, not a session of the track
            if group.empty:
                months[str(month)] = {'value': None, 'reason': 'NO_SESSIONS'}
                continue
            last = group.iloc[-1]
            value = last.nav_base / prev.nav_base - 1 if prev.nav_base > 0 else None
            holding = None if held is None else int((held.reindex(group.date).fillna(0) > 0).sum())
            months[str(month)] = {'value': value, 'reason': None if value is not None else 'NON_POSITIVE_BASE', 'sessions': len(group),
                                  'holding_sessions': holding}
            chain *= 1 + (value or 0.0)
            prev = last
        out[str(year)] = {'months': months, 'year': chain - 1}
    return out
