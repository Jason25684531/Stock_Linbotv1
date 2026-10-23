"""Statistics for development backtests (OD-24). Pure functions; every statistic names the series it is computed on.

Convention: the evidence series is the *daily net excess return over the TAIEX price index* (price-return strategy against a price-return
benchmark), net of the project costs. Sharpe is annualized with 252 sessions and no risk-free rate.
"""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

PERIODS = 252
EULER = 0.5772156649015329
MIN_REGIME_SESSIONS = 60  # fewer sessions in a regime cannot show stability (OD-24)
TOP_DAY_SHARE = 0.05
BOOTSTRAP = {'block_length': 20, 'draws': 2000, 'seed': 20261023, 'confidence': 0.95, 'method': 'moving block bootstrap, percentile interval of the annualized Sharpe'}


def sharpe(x, periods=PERIODS):
    x = np.asarray(x, dtype=float)
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float('nan') if not np.isfinite(sd) or sd < 1e-12 else float(x.mean() / sd * math.sqrt(periods))  # a constant series has no Sharpe


def max_drawdown(returns):
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    return float((equity / np.maximum.accumulate(equity) - 1.0).min())


def cagr(returns, periods=PERIODS):
    r = np.asarray(returns, dtype=float)
    return float(np.prod(1.0 + r) ** (periods / len(r)) - 1.0) if len(r) else float('nan')


def block_bootstrap_sharpe(x, block_length=BOOTSTRAP['block_length'], draws=BOOTSTRAP['draws'], seed=BOOTSTRAP['seed'], confidence=BOOTSTRAP['confidence']):
    """Percentile interval of the annualized Sharpe from `draws` resamples of n sessions built from overlapping blocks of `block_length`."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2 * block_length:
        return {'lower': float('nan'), 'upper': float('nan'), 'draws': 0, 'sessions': n}
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block_length)
    starts = rng.integers(0, n - block_length + 1, size=(draws, blocks))
    index = (starts[:, :, None] + np.arange(block_length)).reshape(draws, -1)[:, :n]
    sample = x[index]
    sd = sample.std(axis=1, ddof=1)
    stat = np.where(sd > 1e-12, sample.mean(axis=1) / np.where(sd > 1e-12, sd, 1) * math.sqrt(PERIODS), np.nan)
    tail = (1 - confidence) / 2
    lower, upper = np.nanquantile(stat, [tail, 1 - tail])
    return {'lower': float(lower), 'upper': float(upper), 'draws': draws, 'sessions': n}


def expected_max_sharpe(n_trials, trial_sharpe_variance):
    """Bailey & Lopez de Prado: the Sharpe that the best of n_trials unskilled trials is expected to reach (per-period units)."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(trial_sharpe_variance) * ((1 - EULER) * norm.ppf(1 - 1 / n_trials) + EULER * norm.ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(x, n_trials, trial_sharpe_variance):
    """Probability that the true Sharpe exceeds the best-of-n_trials benchmark, given the sample's skew and kurtosis (per-period Sharpe)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    sd = x.std(ddof=1)
    if n < 3 or sd < 1e-12:
        return float('nan')
    sr = x.mean() / sd
    z = (x - x.mean()) / sd
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return float('nan')
    return float(norm.cdf((sr - expected_max_sharpe(n_trials, trial_sharpe_variance)) * math.sqrt(n - 1) / math.sqrt(denom)))


def best_days_share(returns, share=TOP_DAY_SHARE):
    """Sum of the best `share` of daily returns divided by the sum of all daily returns (None when the total is not positive)."""
    r = np.sort(np.asarray(returns, dtype=float))[::-1]
    total = r.sum()
    return None if total <= 0 else float(r[:max(1, int(round(len(r) * share)))].sum() / total)


def top_assets_share(pnl_by_asset, count=5):
    """Profit of the `count` best assets divided by total profit (None when the total is not positive)."""
    pnl = pd.Series(pnl_by_asset, dtype=float)
    total = pnl.sum()
    return None if total <= 0 else float(pnl.sort_values(ascending=False).head(count).sum() / total)


def regimes(index_close, window=200):
    """'bull' when the previous session's index close is above its moving average, else 'bear' (no same-day information)."""
    ma = index_close.rolling(window).mean()
    state = np.where(index_close > ma, 'bull', 'bear')
    out = pd.Series(state, index=index_close.index).where(ma.notna())
    return out.shift(1)


def regime_excess(excess, regime):
    """Annualized mean excess return and session count by regime; a regime shorter than MIN_REGIME_SESSIONS is UNDETERMINED."""
    result = {}
    joined = pd.concat([excess.rename('x'), regime.rename('r')], axis=1).dropna()
    for name in ('bull', 'bear'):
        part = joined.loc[joined.r == name, 'x']
        status = 'UNDETERMINED_SAMPLE_TOO_SHORT' if len(part) < MIN_REGIME_SESSIONS else ('NEGATIVE' if part.mean() < 0 else 'POSITIVE')
        result[name] = {'sessions': int(len(part)), 'annualized_excess': float(part.mean() * PERIODS) if len(part) else None, 'status': status}
    return result


def paired_block_bootstrap_sharpe_difference(a, b, block_length=BOOTSTRAP['block_length'], draws=BOOTSTRAP['draws'], seed=BOOTSTRAP['seed'], confidence=BOOTSTRAP['confidence']):
    """Interval of Sharpe(a) - Sharpe(b) when both series are resampled with the same blocks (so their correlation is kept)."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if n < 2 * block_length:
        return {'lower': float('nan'), 'upper': float('nan'), 'difference': float('nan'), 'draws': 0}
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block_length)
    starts = rng.integers(0, n - block_length + 1, size=(draws, blocks))
    index = (starts[:, :, None] + np.arange(block_length)).reshape(draws, -1)[:, :n]

    def annual(x):
        s = x[index]
        sd = s.std(axis=1, ddof=1)
        return np.where(sd > 1e-12, s.mean(axis=1) / np.where(sd > 1e-12, sd, 1) * math.sqrt(PERIODS), np.nan)

    diff = annual(a) - annual(b)
    tail = (1 - confidence) / 2
    lower, upper = np.nanquantile(diff, [tail, 1 - tail])
    return {'lower': float(lower), 'upper': float(upper), 'difference': float(sharpe(a) - sharpe(b)), 'draws': draws}
