"""Engine adapter for historical reference windows (change 2026-11-10-historical-data-foundation-v1, design D6).

Runs the unchanged scripts/reference_engine.execute_one on snapshot inputs by patching four module names it reads at call time
(legacy.START/END/_default_prepared, reference_engine._bars) and legacy.WARMUP_FLOOR while the window is prepared. patch.object raises if
any of those names disappears. This file is part of every historical run identity: keep report and CLI code out of it.
"""
from __future__ import annotations

import functools
from unittest.mock import patch

import pandas as pd

from core.research.history import snapshot as hs

ENGINE_COLUMNS = ['trade_date', 'stock_id', 'open', 'high', 'low', 'close', 'volume', 'amount']  # official_bars.parquet schema


def engine_bars(snap) -> pd.DataFrame:
    return snap.frame('bars')[ENGINE_COLUMNS].copy()


def actions_table(snap) -> dict:
    """corporate_actions.load()-shaped table for the post-run valuation verdict: TWT49U and capital-change dates per ticker."""
    events = snap.frame('events')
    capital = snap.frame('capital_changes')
    rows = [{'ticker': r.stock_id, 'ex_date': str(pd.Timestamp(r.ex_date).date())} for r in events.itertuples()]
    rows += [{'ticker': r.stock_id, 'ex_date': str(pd.Timestamp(r.date).date())} for r in capital.itertuples()]
    bars = snap.frame('bars')
    coverage = {sid: (str(g.min().date()), str(g.max().date())) for sid, g in bars.groupby('stock_id').trade_date}
    return {'events': rows, 'coverage': coverage, 'source_sha256': snap.snapshot_id, 'status': 'SNAPSHOT_TWT49U_TWTAUU_TWTB8U'}


def delistings(snap) -> pd.DataFrame:
    frame = snap.frame('delistings')
    traded = snap.frame('bars')
    traded = traded[traded.status == 'TRADED'].groupby('stock_id').trade_date.agg(['min', 'max'])
    out = frame[['stock_id', 'name', 'delisting_date']].merge(traded.rename(columns={'min': 'first_traded', 'max': 'last_traded'}),
                                                               left_on='stock_id', right_index=True, how='left')
    out['last_traded_before_delisting'] = out.last_traded.notna() & (out.last_traded < out.delisting_date)
    return out


def engine_inputs(snap, start: str, end: str) -> dict:
    """legacy.load_inputs shape on snapshot data; financials, revenue, publications and settings are the pinned engine files."""
    from core.research.native_frame import load_publication_dates
    from scripts import legacy_baseline_run as legacy
    from scripts import native7_rules_only as n7
    for name in ('financial_statements', 'monthly_revenue'):
        if snap.source('pinned', name).resolve() != legacy.input_paths()[name].resolve():
            raise hs.SnapshotDrift(f'PINNED_INPUT_IS_NOT_ENGINE_INPUT: {name}')
    bars = engine_bars(snap)
    first = pd.Timestamp(start) - pd.Timedelta(days=n7.LOOKBACK_DAYS)
    bars = bars[(bars.trade_date >= first) & (bars.trade_date <= pd.Timestamp(end))]
    days = sorted(d.strftime('%Y-%m-%d') for d in bars.trade_date.unique())
    chips = n7.odr.official_chips(days, ('T86', 'MI_MARGN'), n7.read(n7.odr.PREV_CHIPS)['files'])
    frame = bars.rename(columns={'open': 'open_price', 'high': 'high_price', 'low': 'low_price', 'close': 'close_price'}).merge(chips, on=n7.odr.KEYS, how='left')
    frame['short_balance'] = float('nan')
    paths = legacy.input_paths()
    bench = snap.frame('benchmark').set_index('date').taiex_price
    from core.research.history import ledger
    return {'actions': actions_table(snap), 'snapshot': frame, 'financials': pd.read_parquet(paths['financial_statements']),
            'revenue': pd.read_parquet(paths['monthly_revenue']), 'publications': load_publication_dates(), 'settings': n7.read(paths['user_settings']),
            'benchmark': bench, 'delistings': delistings(snap), 'action_ledger': ledger.from_snapshot(snap)}  # POSITION_LEDGER_CA_V1 accounting


@functools.lru_cache(maxsize=2)
def _prepared(snapshot_id, start, end):
    from scripts import legacy_baseline_run as legacy
    snap = hs.load(snapshot_id)
    with patch.object(legacy, 'WARMUP_FLOOR', snap.manifest['window'][0]):
        return legacy._prepare(engine_inputs(snap, start, end), start, end)


@functools.lru_cache(maxsize=1)
def _tquant_bars(snapshot_id):
    bars = engine_bars(hs.load(snapshot_id)).rename(columns={'close': 'close_price', 'high': 'high_price', 'low': 'low_price'})
    bars['stock_id'] = bars['stock_id'].astype(str).str.strip()
    return bars


def execute_window(cap, identity, snapshot_id: str, start: str, end: str) -> dict:
    """reference_engine.execute_one's result dict (nav with nav_base/nav_stress, trades, trades_stress, engine_error) plus the ledger evidence:
    accounting, corporate_actions (events met by held positions), open_positions (window end) and ledger_version."""
    from core.research.reference.runner import check_reference_window
    from scripts import legacy_baseline_run as legacy
    from scripts import reference_engine
    check_reference_window(start, end)
    prep = _prepared(snapshot_id, start, end)
    bars = _tquant_bars(snapshot_id)
    reference_engine.snapshot_history.cache_clear()
    try:
        with patch.object(legacy, 'START', start), patch.object(legacy, 'END', end), \
                patch.object(legacy, '_default_prepared', lambda s, e: prep), patch.object(reference_engine, '_bars', lambda: bars):
            return reference_engine.execute_one(cap, identity) | {'ledger_version': prep.digests.get('action_ledger')}
    finally:
        reference_engine.snapshot_history.cache_clear()


def execute_by_id(strategy_id, identity, snapshot_id, start, end) -> dict:
    """Process-pool entry point (picklable arguments only)."""
    from core.research.reference import catalog
    cap = {c.strategy_id: c for c in catalog.capabilities()}[strategy_id]
    return execute_window(cap, identity, snapshot_id, start, end)


def parity(snap, start='2022-01-03', end=hs.DEV_END) -> dict:
    """Snapshot bars vs the existing official_bars.parquet on their common window: same keys, same OHLCV values."""
    from scripts import legacy_baseline_run as legacy
    theirs = pd.read_parquet(legacy.input_paths()['bars'])
    mine = engine_bars(snap)
    window = lambda f: f[(f.trade_date >= pd.Timestamp(start)) & (f.trade_date <= pd.Timestamp(end))].set_index(['trade_date', 'stock_id']).sort_index()
    mine, theirs = window(mine), window(theirs)
    common = mine.index.intersection(theirs.index)
    a, b = mine.loc[common, ENGINE_COLUMNS[2:]], theirs.loc[common, ENGINE_COLUMNS[2:]]
    equal = ((a == b) | (a.isna() & b.isna())).all(axis=1)
    return {'window': [start, end], 'snapshot_rows': len(mine), 'existing_rows': len(theirs), 'common_rows': len(common),
            'only_in_snapshot': len(mine.index.difference(theirs.index)), 'only_in_existing': len(theirs.index.difference(mine.index)),
            'value_mismatch_rows': int((~equal).sum()), 'status': 'MATCH' if len(mine) == len(theirs) == len(common) and equal.all() else 'DIFFERENT'}
