"""Market regimes fixed before any historical result (design D7) and the per-slice performance of one reference run (design D8).

A session's label uses benchmark closes up to and including that session; the strategy return from session t-1 to t is scored with the
label of t-1, so no label depends on the return it classifies.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REGIME_SPEC = {
    'version': 1, 'series': 'TWSE capitalization-weighted price index (MI_INDEX row)',
    'trend': {'measure': 'close_t / close_(t-120 sessions) - 1', 'BULL': '>= +0.10', 'BEAR': '<= -0.10', 'SIDEWAYS': 'otherwise'},
    'volatility': {'measure': 'stdev(last 60 daily log returns, ddof=1) * sqrt(252)', 'HIGH_VOL': '>= 0.20', 'LOW_VOL': '< 0.20'},
    'scoring': 'return from session t-1 to t uses the label of session t-1', 'min_sessions': 60}
TREND_WINDOW, BULL, BEAR, VOL_WINDOW, HIGH_VOL, MIN_SESSIONS = 120, 0.10, -0.10, 60, 0.20, 60
SEGMENTS = {'2015-2018': range(2015, 2019), '2019-2021': range(2019, 2022), '2022-2025': range(2022, 2026)}
LABELS = ['REFERENCE_ONLY', 'RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES']
TRUSTED = {'VERIFIED_ONLY': ('VERIFIED',), 'VERIFIED_AND_PARTIAL': ('VERIFIED', 'PARTIAL')}


def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


REGIME_SPEC_ID = 'RS1-' + canonical_sha(REGIME_SPEC)[:12]


def register(out: Path) -> dict:
    """Write the spec once (first registration time is kept); a report compares it with the runs' creation times."""
    path = Path(out) / 'regime_specs' / f'{REGIME_SPEC_ID}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, 'x', encoding='utf-8') as stream:
            json.dump({'regime_spec_id': REGIME_SPEC_ID, 'spec': REGIME_SPEC, 'registered_at': datetime.now().astimezone().isoformat(timespec='seconds')},
                      stream, ensure_ascii=False, indent=1)
    return json.loads(path.read_text(encoding='utf-8'))


def labels(bench: pd.DataFrame) -> pd.DataFrame:
    """date, trend, vol, r120, vol60, reason; labels are None with INSUFFICIENT_HISTORY until both windows are full."""
    b = bench.sort_values('date').reset_index(drop=True)
    price = b.taiex_price.astype(float)
    r120 = price / price.shift(TREND_WINDOW) - 1
    vol60 = np.log(price).diff().rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=1) * np.sqrt(252)
    ready = r120.notna() & vol60.notna()
    trend = np.where(r120 >= BULL, 'BULL', np.where(r120 <= BEAR, 'BEAR', 'SIDEWAYS'))
    vol = np.where(vol60 >= HIGH_VOL, 'HIGH_VOL', 'LOW_VOL')
    return pd.DataFrame({'date': b.date, 'trend': pd.Series(trend, dtype=object).where(ready, None),
                         'vol': pd.Series(vol, dtype=object).where(ready, None), 'r120': r120, 'vol60': vol60,
                         'reason': pd.Series(np.where(ready, None, 'INSUFFICIENT_HISTORY'), dtype=object)})


def daily_returns_with_labels(nav: pd.DataFrame, column: str, regime_labels: pd.DataFrame) -> pd.DataFrame:
    frame = nav[['date', column]].assign(date=lambda x: pd.to_datetime(x.date)).sort_values('date')
    frame['ret'] = frame[column].astype(float).pct_change()
    lab = regime_labels.assign(date=pd.to_datetime(regime_labels.date)).sort_values('date')
    lab = lab[['date', 'trend', 'vol']].assign(date_next=lambda x: x.date.shift(-1)).dropna(subset=['date_next'])
    merged = frame.merge(lab.drop(columns='date').rename(columns={'date_next': 'date'}), on='date', how='left')
    return merged.dropna(subset=['ret'])


def _value(v, reason=None):
    return {'value': None if v is None or not np.isfinite(v) else float(v), 'reason': reason if v is None or not np.isfinite(v) else None}


def metrics(returns: pd.Series) -> dict:
    """Compounded statistics of daily returns; every undefined value is None with a reason."""
    r = returns.dropna().astype(float)
    n = len(r)
    if n == 0:
        return {'status': 'NO_SESSIONS', 'sessions': 0, **{k: _value(None, 'NO_SESSIONS') for k in
                ('cumulative_return', 'annualized_return', 'sharpe', 'max_drawdown', 'mean_daily_return', 'se_mean_daily_return', 't_stat')}}
    curve = pd.concat([pd.Series([1.0]), (1 + r).cumprod()], ignore_index=True)  # the starting value is a peak too
    cumulative = curve.iloc[-1] - 1
    std = r.std(ddof=1) if n > 1 else None
    zero = std is None or std == 0
    se = std / np.sqrt(n) if std else None
    out = {'status': 'OK' if n >= MIN_SESSIONS else 'INSUFFICIENT_SAMPLE', 'sessions': n,
           'cumulative_return': _value(cumulative), 'annualized_return': _value((1 + cumulative) ** (252 / n) - 1),
           'sharpe': _value(None, 'ZERO_VOLATILITY') if zero else _value(r.mean() / std * np.sqrt(252)),
           'max_drawdown': _value(float((curve / curve.cummax() - 1).min())), 'mean_daily_return': _value(r.mean()),
           'se_mean_daily_return': _value(None, 'ZERO_VOLATILITY') if zero else _value(se),
           't_stat': _value(None, 'ZERO_VOLATILITY') if zero else _value(r.mean() / se)}
    if n < MIN_SESSIONS:
        for key in ('annualized_return', 'sharpe', 't_stat'):
            out[key] = _value(None, f'INSUFFICIENT_SAMPLE:{n}<{MIN_SESSIONS}')
    return out


def _trades(trades: pd.DataFrame, days: pd.DatetimeIndex, sessions: pd.DatetimeIndex) -> dict:
    if trades is None or trades.empty or not len(days):
        return {'trade_count': 0, 'win_rate': _value(None, 'NO_TRADES'), 'avg_holding_sessions': _value(None, 'NO_TRADES')}
    sell = pd.to_datetime(trades.sell_date, errors='coerce')
    closed = trades[sell.isin(set(days))]
    if closed.empty:
        return {'trade_count': 0, 'win_rate': _value(None, 'NO_TRADES'), 'avg_holding_sessions': _value(None, 'NO_TRADES')}
    held = sessions.searchsorted(pd.to_datetime(closed.sell_date)) - sessions.searchsorted(pd.to_datetime(closed.buy_date))
    return {'trade_count': len(closed), 'win_rate': _value(float((closed.profit_pct.astype(float) > 0).mean())),
            'avg_holding_sessions': _value(float(np.mean(held)))}


def _slice(returns: dict, bench_ret: pd.Series, days, trades, sessions) -> dict:
    out = {track: metrics(r[r.index.isin(days)]) for track, r in returns.items()}
    out['benchmark_total_return'] = metrics(bench_ret[bench_ret.index.isin(days)])
    for track in returns:
        mine, theirs = out[track]['cumulative_return']['value'], out['benchmark_total_return']['cumulative_return']['value']
        out[track]['excess_vs_benchmark'] = _value(None if mine is None or theirs is None else mine - theirs, 'UNDEFINED_COMPONENT')
    out['trades'] = _trades(trades, pd.DatetimeIndex(days), sessions)
    return out


def analyze(nav: pd.DataFrame, trades: pd.DataFrame, bench: pd.DataFrame, trust: dict) -> dict:
    """Years (every year with NAV, metrics only when the year is backtestable), segments, rolling 252 and regimes per trust basis."""
    nav = nav.assign(date=pd.to_datetime(nav.date)).sort_values('date')
    sessions = pd.DatetimeIndex(nav.date)
    returns = {track: pd.Series(nav[col].astype(float).pct_change().to_numpy(), index=sessions).iloc[1:]
               for track, col in (('base', 'nav_base'), ('x2', 'nav_stress'))}
    b = bench.assign(date=pd.to_datetime(bench.date)).set_index('date').taiex_total_return.astype(float)
    bench_ret = b.pct_change(fill_method=None).reindex(sessions)  # a missing index value leaves its two returns undefined, never filled
    lab = labels(bench)
    tagged = daily_returns_with_labels(nav, 'nav_base', lab).set_index('date')
    years = {}
    for year in sorted(set(sessions.year)):
        status = trust.get(year, 'NOT_EVALUATED')
        days = returns['base'].index[returns['base'].index.year == year]
        if status in TRUSTED['VERIFIED_AND_PARTIAL']:
            years[str(year)] = {'trust': status, **_slice(returns, bench_ret, days, trades, sessions)}
        else:
            blank = {k: _value(None, f'DATA_TRUST_{status}') for k in ('cumulative_return', 'annualized_return', 'sharpe', 'max_drawdown')}
            years[str(year)] = {'trust': status, 'base': blank, 'x2': blank, 'sessions': len(days)}
    out = {'labels': LABELS, 'regime_spec_id': REGIME_SPEC_ID, 'years': years, 'segments': {}, 'regimes': {}, 'rolling_252': {},
           'regime_sessions': {}}
    nav_years = set(sessions.year)
    for basis, allowed in TRUSTED.items():
        good = {y for y in nav_years if trust.get(y) in allowed}
        out['segments'][basis] = {}
        for name, span in SEGMENTS.items():
            days = returns['base'].index[returns['base'].index.year.isin(good & set(span))]
            out['segments'][basis][name] = {'years_used': sorted(good & set(span)),
                                            'excluded_years': {str(y): trust.get(y, 'NO_NAV') if y in nav_years else 'NO_NAV' for y in span if y not in good},
                                            **_slice(returns, bench_ret, days, trades, sessions)}
        usable = returns['base'].index[returns['base'].index.year.isin(good)]
        out['regimes'][basis] = {}
        for column, values in (('trend', ('BULL', 'BEAR', 'SIDEWAYS')), ('vol', ('HIGH_VOL', 'LOW_VOL'))):
            for value in values:
                days = tagged.index[(tagged[column] == value) & tagged.index.isin(usable)]
                out['regimes'][basis][value] = {'sessions': len(days), **_slice(returns, bench_ret, days, trades, sessions)}
        rolling = (1 + returns['base']).rolling(252).apply(np.prod, raw=True) - 1
        inside = rolling[[all(d.year in good for d in sessions[max(0, i - 251): i + 1]) for i in range(1, len(sessions))]].dropna()
        out['rolling_252'][basis] = {'windows': len(inside), 'min': _value(inside.min() if len(inside) else None, 'NO_FULL_WINDOW'),
                                     'median': _value(inside.median() if len(inside) else None, 'NO_FULL_WINDOW'),
                                     'max': _value(inside.max() if len(inside) else None, 'NO_FULL_WINDOW'),
                                     'share_positive': _value(float((inside > 0).mean()) if len(inside) else None, 'NO_FULL_WINDOW')}
    out['unlabelled_sessions'] = int(tagged.trend.isna().sum())  # no regime label (short history or a missing index value)
    out['regime_sessions'] = {v: int((tagged[c] == v).sum()) for c, vs in (('trend', ('BULL', 'BEAR', 'SIDEWAYS')), ('vol', ('HIGH_VOL', 'LOW_VOL'))) for v in vs}
    return out
