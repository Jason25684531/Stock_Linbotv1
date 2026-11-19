"""V7 rules: point-in-time revenue, revenue momentum selection, the ETF trend switch and the temporary engine adjustments that let the
V2 engine hold many names to the next rebalance. Every V7 run is bound to this file's functions, so keep the program code out of it.

Nothing here edits an engine or strategy file: constants and host attributes are patched inside a context and restored on exit.
"""
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'outputs/strategy_v7/data'


def usable_revenue(frame, asof):
    """Rows whose release date is strictly before the signal day: revenue is usable from the day after its release."""
    return frame[frame.index < pd.Timestamp(asof)]


def revenue_momentum_ids(frame, asof):
    """Stocks whose latest three months average above their twelve months average. Needs twelve consecutive monthly rows and no
    missing value in them; a missing month stays missing and excludes the stock."""
    rows = usable_revenue(frame, asof).tail(12)
    if len(rows) < 12:
        return []
    months = rows.index.year * 12 + rows.index.month
    if (np.diff(months) != 1).any():
        return []
    full = rows.dropna(axis=1)
    return sorted(str(sid) for sid in full.columns[(full.tail(3).mean() > full.mean()).values])


def price_trend_ids(history, min_volume):
    """Close above the 20, 60 and 120 session averages and a 20 session average volume of at least `min_volume` shares."""
    keep = []
    for sid, group in history.groupby('stock_id', sort=True):
        close = group.close_price.astype(float)
        if len(close) < 120 or (close.tail(120) <= 0).any() or group.volume.astype(float).tail(20).mean() < min_volume:
            continue
        last = close.iloc[-1]
        if last > close.tail(20).mean() and last > close.tail(60).mean() and last > close.tail(120).mean():
            keep.append(str(sid))
    return keep


def revenue_momentum_selection(history, revenue, top, min_volume):
    """Revenue momentum and price trend filters, ranked by the 5 session average daily change, best first, top `top`."""
    asof = history.trade_date.max()
    chosen = set(revenue_momentum_ids(revenue, asof)) & set(price_trend_ids(history, min_volume))
    scores = {}
    for sid, group in history[history.stock_id.astype(str).isin(chosen)].groupby('stock_id', sort=True):
        if group.trade_date.iloc[-1] == asof:
            scores[str(sid)] = float(group.close_price.astype(float).pct_change().tail(5).mean())
    return sorted(scores, key=lambda s: (-scores[s], s))[:top]


def etf_switch(frame, ma, costs, multiple):
    """Hold the ETF while its close two sessions ago was above its `ma`-session average, else cash: a signal on a close is traded at
    the next session's close. Each entry pays costs['buy'] and each exit costs['sell'], times `multiple`. Price only: no dividends."""
    data = frame.assign(date=pd.to_datetime(frame.date)).sort_values('date').reset_index(drop=True)
    close = data.close.astype(float)
    above = (close > close.rolling(ma).mean()).astype(int)
    position = above.shift(2).fillna(0).astype(int)
    daily = close.pct_change().fillna(0.0)
    nav, values = 1.0, []
    for i in range(len(data)):
        change = position[i] - (position[i - 1] if i else 0)
        if change > 0:
            nav *= 1 - costs['buy'] * multiple
        elif change < 0:
            nav *= 1 - costs['sell'] * multiple
        nav *= 1 + position[i] * daily[i]
        values.append(nav)
    return pd.DataFrame({'date': data.date, 'nav': values, 'position': position})


@lru_cache(maxsize=1)
def load_revenue(path=None):
    return pd.read_parquet(path or DATA / 'revenue_usable.parquet')


def _ranked_find_candidates(top):
    """find_candidates of the engine's rules-only path, returning `top` names instead of 3."""
    def find(self, date_str):
        df, exclusions = self.research_data.frame(date_str, self.required_inputs)
        self.data_exclusions.append(exclusions)
        if df is None or df.empty:
            return []
        candidates = runner_module().select_backtest_candidates(self.strategy_obj, getattr(self, 'strategy_runner', None),
                                                                getattr(self, '_selection_strategy_name', self.mode), df, date_str,
                                                                getattr(self, '_strategy_runtime_overrides', {}))
        return [] if candidates.empty else candidates.head(top)['stock_id'].tolist()
    return find


def runner_module():
    import core.backtest.runner as runner
    return runner


def first_sessions(sessions, frequency):
    """The first session of each month ('monthly'), or of each month on or after the 13th ('monthly_after_12'): monthly revenue is
    released by the 10th to 12th, so a signal from the 13th on sees the latest month."""
    dates = pd.Series(sorted(pd.to_datetime(list(sessions))))
    if frequency == 'monthly_after_12':
        dates = dates[dates.dt.day >= 13]
    return set(dates.groupby(dates.dt.to_period('M')).first())


class _Weight:
    """Stand-in for runner.POSITION_SIZE: `initial_capital * weight` is the book value set at the last rebalance divided by `top`, so
    each buy is one equal share of what the book is worth now instead of one share of the starting cash."""

    def __init__(self, top):
        self.top, self.equity = top, None

    def __rmul__(self, capital):
        return (capital if self.equity is None else self.equity) / self.top


def _book_value(engine):
    """Cash plus every position at its last stored mark. runner.run marks the positions at the signal day's close before it calls the step,
    so this is a close already known when the signal is made. Never mark again here: a second mark would count a stale day twice."""
    held = sum(info['shares'] * (info.get('last_valid_close') or info.get('last_price') or info['cost']) for info in engine.positions.values())
    return engine.capital + held


def rebalance_step(engine, session, book):
    """One close of the V7 book. Not a rebalance day: nothing. A rebalance day: sell every held name that is not in the new top `top`
    (sells are queued before buys, so both fill at the next open and a repeating name is kept, not sold and skipped); buy the new names
    only when entries are open (A2 closes them while the index is below its average). No stop, no profit target, no engine trend gate."""
    day = pd.Timestamp(session).normalize()
    if day not in book['rebalance']:
        return
    book['weight'].equity = _book_value(engine)
    target = [] if book.get('exit_when_closed') and day not in book['entries'] else book['choose'](engine, session)
    for sid in list(engine.positions):
        engine.positions[sid]['days'] += 1
        curr = engine.get_data(sid, session)
        if curr and sid not in target:
            engine._exit_at_signal(sid, curr['close_price'], session, 'REBALANCE')
    if day not in book['entries']:
        return
    for sid in target:
        if sid not in engine.positions:
            engine.pending_orders.append({'side': 'BUY', 'stock_id': sid, 'signal_date': session, 'reason': 'ENTRY_SIGNAL'})


@contextmanager
def portfolio_rule(params, sessions, revenue, bench=None):
    """Hold up to `top` names at equal weight, re-ranked on the first session after the 12th of each month (the registered `hold_sessions`
    field is superseded: a name stays until a rebalance drops it). With `market_ma`, new names enter only while the index is above that
    average. The host's own selection is replaced by revenue momentum and the engine's stepped stop, time exit and BULL gate are bypassed
    by replacing process_close_signals for the length of the run."""
    from scripts import strategy_v5_rules
    from scripts import strategy_v6_rules
    from scripts.strategy_v4_ranking import ranked_filter
    runner = runner_module()
    host = strategy_v5_rules.STRATEGY_CLASSES[params['host']]
    rebalance = first_sessions(sessions, params['entry'])
    entries = rebalance & strategy_v6_rules.market_above(bench, params['market_ma']) if params.get('market_ma') else rebalance
    weight = _Weight(params['top'])
    book = {'rebalance': rebalance, 'entries': entries, 'weight': weight, 'choose': _ranked_find_candidates(params['top']),
            'exit_when_closed': params.get('exit_when_closed', False)}
    with ExitStack() as stack:
        stack.enter_context(patch.object(runner, 'MAX_HOLDINGS', params['top']))
        stack.enter_context(patch.object(runner, 'POSITION_SIZE', weight))
        stack.enter_context(patch.object(runner.BacktestEngine, 'process_close_signals', lambda self, session, trend: rebalance_step(self, session, book)))
        stack.enter_context(patch.object(host, 'select_history', lambda self, history: revenue_momentum_selection(history, revenue, params['top'], params['min_volume'])))
        stack.enter_context(patch.object(host, 'filter_candidates', ranked_filter))
        yield
