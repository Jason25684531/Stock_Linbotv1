"""Canonical-named operators. Rolling/lag names delegate to core.research.factors."""

import pandas as pd

from core.research.factors import delta as _delta
from core.research.factors import ts_corr, ts_max, ts_mean, ts_min, ts_std


def delay(values: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
    """Shift a wide frame back by `periods`; never exposes a future observation."""

    return values.shift(periods)


delta = _delta
rolling_mean = ts_mean
rolling_std = ts_std
rolling_max = ts_max
rolling_min = ts_min
rolling_corr = ts_corr


def rank_cs(values: pd.DataFrame) -> pd.DataFrame:
    """Rank across securities (columns) within each trade date (row)."""

    return values.rank(axis=1)


def rank_ts(values: pd.DataFrame, window: int) -> pd.DataFrame:
    """Rank each security against its own trailing history; never across securities."""

    return values.rolling(window, min_periods=window).apply(lambda w: w.rank().iloc[-1], raw=False)


def winsorize_cs(values: pd.DataFrame, lower: float, upper: float) -> pd.DataFrame:
    """Clip each trade date's cross-section to that date's own quantile bounds."""

    clean = values.replace([float("inf"), float("-inf")], float("nan"))
    sample_size = clean.notna().sum(axis=1)
    lower_bound = clean.quantile(lower, axis=1)
    upper_bound = clean.quantile(upper, axis=1)
    clipped = clean.clip(lower=lower_bound, upper=upper_bound, axis=0)
    return clipped.where(sample_size.ge(2), other=float("nan"))
