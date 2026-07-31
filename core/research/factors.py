"""Pure factor operators with explicit missing-value semantics."""

import pandas as pd


def ts_mean(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values.rolling(window, min_periods=window).mean()


def ts_std(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values.rolling(window, min_periods=window).std(ddof=1)


def ts_max(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values.rolling(window, min_periods=window).max()


def ts_min(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values.rolling(window, min_periods=window).min()


def ts_corr(left: pd.DataFrame, right: pd.DataFrame, window: int) -> pd.DataFrame:
    return left.rolling(window, min_periods=window).corr(right).where(left.rolling(window, min_periods=window).std().ne(0) & right.rolling(window, min_periods=window).std().ne(0))


def delta(values: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
    return values.diff(periods)


def pct_change(values: pd.DataFrame) -> pd.DataFrame:
    return values.pct_change(fill_method=None)


def wilder_atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int) -> pd.DataFrame:
    previous_close = close.shift(1)
    true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).groupby(level=0, axis=1).max()
    result = true_range.copy() * float("nan")
    for column in true_range:
        seed = true_range[column].iloc[:window].mean()
        if len(true_range) >= window and true_range[column].iloc[:window].notna().all():
            result.iloc[window - 1, result.columns.get_loc(column)] = seed
            for row in range(window, len(result)):
                result.iloc[row, result.columns.get_loc(column)] = (result.iloc[row - 1, result.columns.get_loc(column)] * (window - 1) + true_range.iloc[row, true_range.columns.get_loc(column)]) / window
    return result
