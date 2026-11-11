"""Engine side of the reference batch (change 2026-11-09-market-data-autonomy-and-reference-research-v1, design D7).

Runs one strategy's base and 2x cost tracks through the governed engine path of scripts/legacy_baseline_run.py (unchanged) on the pinned
native7 research snapshot. TQuant ports read the same official bars through snapshot_history instead of MySQL. It lives in scripts/ because
it patches production strategy internals (core.strategies._tquant), which core.research must not import (test_package_boundaries).
"""
from __future__ import annotations

import functools

from core.research.reference.runner import check_reference_window


@functools.lru_cache(maxsize=1)
def _bars():
    import pandas as pd
    from scripts import legacy_baseline_run as legacy
    bars = pd.read_parquet(legacy.input_paths()['bars']).rename(columns={'close': 'close_price', 'high': 'high_price', 'low': 'low_price'})
    bars['stock_id'] = bars['stock_id'].astype(str).str.strip()
    return bars


@functools.lru_cache(maxsize=4)
def snapshot_history(asof_date):
    """core.strategies._tquant.load_history on the pinned official bars instead of MySQL: same 550-day window, MARKET_SYMBOL anchors,
    four-digit ids and de-duplication."""
    import pandas as pd
    from config import Config
    check_reference_window(asof_date, asof_date)
    bars, end = _bars(), pd.Timestamp(asof_date)
    history = bars[(bars.trade_date >= end - pd.Timedelta(days=550)) & (bars.trade_date <= end)][
        ['stock_id', 'trade_date', 'close_price', 'high_price', 'low_price', 'volume']]
    anchors = history.loc[history.stock_id.eq(Config.MARKET_SYMBOL), 'trade_date']
    history = history.loc[history.trade_date.isin(anchors) & history.stock_id.str.fullmatch(r'[1-9]\d{3}')]
    return history.sort_values(['stock_id', 'trade_date']).drop_duplicates(['stock_id', 'trade_date'], keep='last')


def execute_one(cap, identity) -> dict:
    """Both cost tracks through the governed engine path; selections are computed once and shared by the two tracks."""
    import contextlib
    from unittest.mock import patch
    import pandas as pd
    import core.backtest.runner as engine_runner
    from scripts import legacy_baseline_run as legacy
    check_reference_window(legacy.START, legacy.END)
    prep = legacy._default_prepared(legacy.START, legacy.END)
    select, memo = engine_runner.select_backtest_candidates, {}

    def shared_select(strategy, platform, name, frame, asof_date, runtime_overrides=None):
        key = (name, str(asof_date))
        if key not in memo:
            memo[key] = select(strategy, platform, name, frame, asof_date, runtime_overrides)
        return memo[key].copy()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(engine_runner, 'select_backtest_candidates', shared_select))
        if cap.engine == 'TQUANT_SNAPSHOT':
            from core.strategies import _tquant
            stack.enter_context(patch.object(_tquant, 'load_history', snapshot_history))
        base, _, _ = legacy._run_track(cap.strategy_id, {}, prep, 1)
        stress, _, _ = legacy._run_track(cap.strategy_id, {}, prep, legacy.STRESS_MULTIPLE)
    nav = pd.DataFrame({'date': [d for d, _ in base['nav']], 'nav_base': [v for _, v in base['nav']], 'nav_stress': [v for _, v in stress['nav']]})
    return {'nav': nav, 'trades': pd.DataFrame(base['trades']), 'trades_stress': pd.DataFrame(stress['trades']),
            'engine_error': base['engine_error'] or stress['engine_error'], 'accounting': base['accounting'],
            'corporate_actions': pd.DataFrame(base['corporate_action_events']), 'open_positions': base.get('open_positions', []),
            'cost_summary': base.get('cost_summary')}
