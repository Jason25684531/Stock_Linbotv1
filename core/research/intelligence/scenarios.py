"""One-year historical scenarios: circular block bootstrap of VERIFIED-track daily NAV returns (design D7, sample rule SR1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.validation.bootstrap import bootstrap_metrics

RULE = {'id': 'SR1', 'version': 1, 'complete_trusted_years': 3,
        'definition': 'at least 3 consecutive years whose trust is VERIFIED and whose NAV dates equal the official calendar sessions of that year exactly'}
SEED, PATHS, HORIZON, BLOCK, SENSITIVITY = 20261111, 2000, 252, 20, (5, 10, 60)
CHECKPOINTS = (63, 126, 189, 252)
NOTICE = '歷史情境模擬，非未來價格或投資報酬預測。重抽樣的是既有回測的每日淨值報酬，無法模擬策略在新的市場路徑下重新選股的行為。'
NOTICE_EN = 'Historical scenario simulation; NOT a forecast of future prices or returns. Resampling stored daily NAV returns cannot simulate the strategy selecting different stocks on a new market path.'


def sample_check(nav_dates, calendar_sessions, status: dict) -> dict:
    """Per-year completeness against the official calendar, then the longest run of consecutive complete trusted years."""
    nav_dates, cal = pd.DatetimeIndex(nav_dates), pd.DatetimeIndex(calendar_sessions)
    years = []
    for year in sorted(set(nav_dates.year)):
        official, mine = set(cal[cal.year == year]), set(nav_dates[nav_dates.year == year])
        missing, extra = sorted(official - mine), sorted(mine - official)
        trust = status.get(year, 'NOT_EVALUATED')
        years.append({'year': int(year), 'trust': trust, 'official_sessions': len(official), 'nav_sessions': len(mine),
                      'missing': [d.strftime('%Y-%m-%d') for d in missing[:20]], 'extra': [d.strftime('%Y-%m-%d') for d in extra[:20]],
                      'complete': trust == 'VERIFIED' and not missing and not extra and bool(official)})
    best, current = None, None
    for y in years:
        if y['complete']:
            current = [current[0], y['year']] if current and current[1] == y['year'] - 1 else [y['year'], y['year']]
            if best is None or current[1] - current[0] > best[1] - best[0]:
                best = current
        else:
            current = None
    enough = best is not None and best[1] - best[0] + 1 >= RULE['complete_trusted_years']
    return {'rule': RULE, 'years': years, 'span': best, 'status': 'SUFFICIENT' if enough else 'INSUFFICIENT_VERIFIED_HISTORY'}


def _paths(returns: np.ndarray, rng, paths: int, horizon: int, block: int) -> np.ndarray:
    n = len(returns)
    starts = rng.integers(0, n, size=(paths, -(-horizon // block)))
    index = (starts[:, :, None] + np.arange(block)) % n
    return returns[index.reshape(paths, -1)[:, :horizon]]


def _quantiles(values) -> dict:
    values = np.asarray(values, dtype=float)
    return {'median': float(np.median(values)), 'p05': float(np.percentile(values, 5)), 'p95': float(np.percentile(values, 95)),
            'min': float(values.min()), 'max': float(values.max())}


def _histogram(values, bins=30) -> dict:
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {'counts': [], 'edges': []}
    counts, edges = np.histogram(values, bins=bins)
    return {'counts': counts.tolist(), 'edges': [float(e) for e in edges]}


def _drawdown_and_underwater(sim: np.ndarray):
    curve = np.cumprod(1 + sim, axis=1)
    curve = np.hstack([np.ones((len(sim), 1)), curve])
    peak = np.maximum.accumulate(curve, axis=1)
    under = curve < peak
    mdd = (curve / peak - 1).min(axis=1)
    longest, censored = np.zeros(len(sim), dtype=int), np.zeros(len(sim), dtype=bool)
    for i, row in enumerate(under):
        best = run = 0
        for flag in row:
            run = run + 1 if flag else 0
            best = max(best, run)
        longest[i], censored[i] = best, bool(row[-1])  # still under water at the last session: the open spell is a lower bound
    return mdd, longest, censored, under


def simulate(returns, seed=SEED, paths=PATHS, horizon=HORIZON, block=BLOCK, sensitivity=SENSITIVITY) -> dict:
    returns = np.asarray(returns, dtype=float)
    rng = np.random.default_rng(seed)
    sim = _paths(returns, rng, paths, horizon, block)
    mdd, longest, censored, under = _drawdown_and_underwater(sim)
    boot = bootstrap_metrics(returns.tolist(), samples=paths, seed=seed, method='block', block_size=block)
    cagr = np.asarray(boot.distributions.get('cagr', []), dtype=float)
    out = {'seed': seed, 'paths': paths, 'horizon': horizon, 'block': block, 'sample_sessions': len(returns), 'notice': NOTICE, 'notice_en': NOTICE_EN,
           'drawdown': _quantiles(mdd) | {'histogram': _histogram(mdd)},
           'underwater': {'recovered_histogram': _histogram(longest[~censored]), 'censored_histogram': _histogram(longest[censored]),
                          'censored_share': float(censored.mean()),
                          'longest_median': float(np.median(longest[~censored])) if (~censored).any() else None,
                          'censored_longest_median_lower_bound': float(np.median(longest[censored])) if censored.any() else None,
                          'share_underwater_at': {str(k): float(under[:, k].mean()) for k in CHECKPOINTS if k <= horizon},
                          'definition': 'longest run of sessions below the running high within the path; a path still below its high at the last session is CENSORED '
                                        'and its duration is only a lower bound (never counted as recovered at 252)'},
           'cagr': (_quantiles(cagr) | {'histogram': _histogram(cagr), 'interval_95': [float(np.percentile(cagr, 2.5)), float(np.percentile(cagr, 97.5))],
                                        'share_below_zero': float((cagr < 0).mean()), 'share_below_5pct': float((cagr < 0.05).mean()),
                                        'definition': f'block bootstrap (block {block}) of the full verified sample length, {paths} resamples'})
           if len(cagr) else {'reason': 'NO_DEFINED_CAGR'}}
    out['sensitivity'] = {}
    for b in sensitivity:
        s = _paths(returns, np.random.default_rng(seed), paths, horizon, b)
        dd, _, cens, _ = _drawdown_and_underwater(s)
        out['sensitivity'][str(b)] = {'drawdown_median': float(np.median(dd)), 'drawdown_p05': float(np.percentile(dd, 5)), 'censored_share': float(cens.mean())}
    return out


def _window_mdd(curve: np.ndarray) -> float:
    return float((curve / np.maximum.accumulate(curve) - 1).min())


def stress(nav: pd.Series, bench: pd.Series | None, window=HORIZON) -> dict:
    """Two actual-history windows of `window` sessions: the strategy's worst, and the benchmark's worst with the strategy's result then."""
    nav = nav.astype(float)
    out = {}
    if len(nav) <= window:
        return {'strategy_worst_252': {'reason': 'SHORTER_THAN_WINDOW'}, 'benchmark_worst_252': {'reason': 'SHORTER_THAN_WINDOW'}}
    ret = nav.to_numpy()[window:] / nav.to_numpy()[:-window] - 1
    i = int(np.argmin(ret))
    start, end = nav.index[i], nav.index[i + window]
    out['strategy_worst_252'] = {'start': start.strftime('%Y-%m-%d'), 'end': end.strftime('%Y-%m-%d'), 'return': float(ret[i]),
                                 'max_drawdown': _window_mdd(nav.iloc[i:i + window + 1].to_numpy())}
    values = nav.to_numpy()
    dds = [_window_mdd(values[k:k + window + 1]) for k in range(len(values) - window)]
    k = int(np.argmin(dds))
    out['worst_window_max_drawdown'] = {'start': nav.index[k].strftime('%Y-%m-%d'), 'end': nav.index[k + window].strftime('%Y-%m-%d'), 'value': dds[k],
                                        'definition': 'deepest drawdown inside any window of 252 sessions'}
    if bench is None or bench.reindex(nav.index).isna().any():
        out['benchmark_worst_252'] = {'reason': 'BENCHMARK_MISSING'}
        return out
    b = bench.reindex(nav.index).astype(float)
    bret = b.to_numpy()[window:] / b.to_numpy()[:-window] - 1
    j = int(np.argmin(bret))
    out['benchmark_worst_252'] = {'start': nav.index[j].strftime('%Y-%m-%d'), 'end': nav.index[j + window].strftime('%Y-%m-%d'),
                                  'benchmark_return': float(bret[j]), 'strategy_return': float(ret[j]),
                                  'strategy_max_drawdown': _window_mdd(nav.iloc[j:j + window + 1].to_numpy())}
    return out
