"""V6 hypothesis rules. Every V6 run is bound to this file's hash, so keep the program and report code out of it.

A rule receives data already cut at its window end (`until`), so it cannot see the holdout or later sessions.
"""
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pandas as pd


def until(frame, end, column='date'):
    return frame[pd.to_datetime(frame[column]) <= pd.Timestamp(end)].reset_index(drop=True)


def index_switch(bench, ma, costs, multiple):
    """Hold the total-return index while yesterday's close was above its `ma`-session average, else cash at zero return.
    Each entry pays costs['buy'] and each exit costs['sell'], times `multiple`."""
    frame = bench.assign(date=pd.to_datetime(bench.date)).sort_values('date').reset_index(drop=True)
    price = frame.taiex_total_return.astype(float)
    above = (price > price.rolling(ma).mean()).astype(int)
    position = above.shift(1).fillna(0).astype(int)
    daily = price.pct_change().fillna(0.0)
    nav, values = 1.0, []
    for i in range(len(frame)):
        change = position[i] - (position[i - 1] if i else 0)
        if change > 0:
            nav *= 1 - costs['buy'] * multiple
        elif change < 0:
            nav *= 1 - costs['sell'] * multiple
        nav *= 1 + position[i] * daily[i]
        values.append(nav)
    return pd.DataFrame({'date': frame.date, 'nav': values, 'position': position})


def mix(navs, weights):
    """Sleeves rebalanced to `weights` on the first session of each month; sleeve NAVs already carry their own costs.
    ponytail: rebalancing between sleeves is free here; charge it if a sleeve's monthly drift becomes large."""
    frames = {name: nav.assign(date=pd.to_datetime(nav.date)).set_index('date').nav.astype(float) for name, nav in navs.items()}
    table = pd.DataFrame(frames).dropna()
    returns = table.pct_change().fillna(0.0)
    value = {name: weights[name] for name in table}
    rows, month = [], None
    for date, r in returns.iterrows():
        for name in table:
            value[name] *= 1 + r[name]
        total = sum(value.values())
        if month is not None and date.to_period('M') != month:
            value = {name: total * weights[name] for name in table}
        month = date.to_period('M')
        rows.append({'date': date, 'nav': total} | {f'weight_{name}': value[name] / total for name in table})
    return pd.DataFrame(rows)


def first_sessions(sessions, frequency):
    dates = pd.Series(sorted(pd.to_datetime(list(sessions))))
    key = dates.dt.to_period('M' if frequency == 'monthly' else 'Q')
    return set(dates.groupby(key).first())


@contextmanager
def entry_only_on(cls, dates):
    """The host strategy proposes candidates only on `dates`; exits stay with the engine and the host's own rules."""
    original = cls.filter_candidates

    def gated(self, df):
        if df.empty or pd.Timestamp(df.trade_date.max()).normalize() not in dates:
            return df.iloc[:0].copy()
        return original(self, df)
    with patch.object(cls, 'filter_candidates', gated):
        yield


def risk_adjusted_momentum(history, lookback, top, min_volume):
    """Return over `lookback` sessions divided by the standard deviation of daily returns over the same sessions, best first;
    only stocks with a full, positive price history ending on the latest session and enough 20-session average volume."""
    from core.research.strategy_v4 import rank_scores
    asof, scores = history.trade_date.max(), {}
    for sid, group in history.groupby('stock_id', sort=True):
        group = group.tail(lookback + 1)
        close = group.close_price.astype(float)
        if len(group) <= lookback or group.trade_date.iloc[-1] != asof or (close <= 0).any():
            continue
        if group.volume.astype(float).tail(20).mean() < min_volume:
            continue
        volatility = close.pct_change().dropna().std()
        if volatility > 1e-8:
            scores[sid] = (close.iloc[-1] / close.iloc[0] - 1) / volatility
    return rank_scores(scores)[:top]


def market_above(bench, ma):
    """Sessions whose total-return close is above its `ma`-session average, using only data up to that session."""
    frame = bench.assign(date=pd.to_datetime(bench.date)).sort_values('date')
    price = frame.taiex_total_return.astype(float)
    return set(frame.date[(price > price.rolling(ma).mean()).values])


@contextmanager
def engine_rule(params, sessions, bench=None):
    """`params`: host strategy id; optional V5 correction, replacement selection, entry frequency and market gate.
    Exits stay with the engine and the host's risk parameters."""
    from scripts import strategy_v5_rules
    from scripts.strategy_v4_ranking import ranked_filter
    host = strategy_v5_rules.STRATEGY_CLASSES[params['host']]
    with ExitStack() as stack:
        if params.get('v5_correction'):
            stack.enter_context(strategy_v5_rules.applied(params['host']))
        if params.get('select') == 'risk_adjusted_momentum':
            pick = lambda self, history: risk_adjusted_momentum(history, params['lookback'], params['top'], params['min_volume'])
            stack.enter_context(patch.object(host, 'select_history', pick))
            stack.enter_context(patch.object(host, 'filter_candidates', ranked_filter))
        dates = first_sessions(sessions, params['entry']) if params.get('entry') else set(pd.to_datetime(list(sessions)))
        if params.get('market_ma'):
            dates &= market_above(bench, params['market_ma'])
        if params.get('entry') or params.get('market_ma'):
            stack.enter_context(entry_only_on(host, dates))
        yield
