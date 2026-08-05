"""Pure factor operators with explicit missing-value semantics."""

import math
from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class FactorResult:
    values: pd.DataFrame
    diagnostics: list[dict[str, object]]


@dataclass(frozen=True)
class FactorSpec:
    name: str
    version: str
    family: str
    fn: Callable[..., pd.DataFrame]
    required_columns: tuple[str, ...]
    lookback: int
    direction: int
    price_basis: str
    unit: str
    description: str
    canonical: bool = True


def _ratio(numerator: pd.DataFrame, denominator: pd.DataFrame) -> pd.DataFrame:
    return numerator.div(denominator.where(denominator.ne(0))).replace([float("inf"), float("-inf")], float("nan"))


def _momentum_20d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = frames["adjusted_close"]
    return _ratio(close, close.shift(20)) - 1


def _momentum_60d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = frames["adjusted_close"]
    return _ratio(close, close.shift(60)) - 1


def _momentum_12_1(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = frames["adjusted_close"]
    return _ratio(close.shift(21), close.shift(252)) - 1


def _near_high_252d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = frames["adjusted_close"]
    return _ratio(close, ts_max(close, 252)) - 1


def _return_5d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = frames["adjusted_close"]
    return _ratio(close, close.shift(5)) - 1


def _reversal_5d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return -_return_5d(frames)


def _volume_ratio_20d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    volume = frames["volume"].where(frames["volume"] > 0)
    return _ratio(volume, ts_mean(volume, 20))


def _price_volume_corr_20d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    volume = frames["volume"].where(frames["volume"] > 0)
    return ts_corr(pct_change(frames["adjusted_close"]), volume.map(math.log).diff(), 20)


def _range_position(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return _ratio(frames["adjusted_close"] - frames["adjusted_low"], frames["adjusted_high"] - frames["adjusted_low"])


def _realized_vol_20d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return ts_std(pct_change(frames["adjusted_close"]), 20) * math.sqrt(252)


def _natr_14d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return _ratio(wilder_atr(frames["adjusted_high"], frames["adjusted_low"], frames["adjusted_close"], 14), frames["adjusted_close"])


def _amihud_20d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    values = _ratio(pct_change(frames["adjusted_close"]).abs(), frames["amount"])
    return ts_mean(values.where(frames["volume"] > 0), 20)


def _overnight_gap_20d(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return ts_mean(_ratio(frames["adjusted_open"], frames["adjusted_close"].shift(1)) - 1, 20)


def _vwap_gap(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    volume = frames["volume"].where(frames["volume"] > 0)
    raw_vwap = _ratio(frames["amount"], volume)
    return _ratio(frames["raw_close"], raw_vwap) - 1


def _spec(
    name: str, family: str, fn: Callable[..., pd.DataFrame], required_columns: tuple[str, ...], lookback: int,
    price_basis: str, unit: str, description: str, *, direction: int, canonical: bool = True,
) -> FactorSpec:
    return FactorSpec(name, "1", family, fn, required_columns,
                      lookback, direction, price_basis, unit, description, canonical)


_UNDETERMINED_DESCRIPTION = " (direction undetermined pending Rank IC / quantile-return research)"

FACTOR_REGISTRY = {
    spec.name: spec
    for spec in (
        _spec("momentum_20d", "momentum", _momentum_20d, ("adjusted_close",), 21, "local_adjusted", "ratio", "20-day momentum", direction=1),
        _spec("momentum_60d", "momentum", _momentum_60d, ("adjusted_close",), 61, "local_adjusted", "ratio", "60-day momentum", direction=1),
        _spec("momentum_12_1", "momentum", _momentum_12_1, ("adjusted_close",), 253, "local_adjusted", "ratio", "12-month momentum excluding one month", direction=1),
        _spec("near_high_252d", "momentum", _near_high_252d, ("adjusted_close",), 252, "local_adjusted", "ratio", "distance from 252-day high", direction=1),
        _spec("reversal_5d", "reversal", _reversal_5d, ("adjusted_close",), 6, "local_adjusted", "ratio", "negated 5-day return, a short-horizon reversal signal", direction=1),
        _spec("return_5d", "return", _return_5d, ("adjusted_close",), 6, "local_adjusted", "ratio", "5-day return" + _UNDETERMINED_DESCRIPTION, direction=0, canonical=False),
        _spec("vwap_gap", "return", _vwap_gap, ("raw_close", "amount", "volume"), 1, "raw_unadjusted", "ratio", "unadjusted close vs. same-day VWAP" + _UNDETERMINED_DESCRIPTION, direction=0),
        _spec("volume_ratio_20d", "volume", _volume_ratio_20d, ("volume",), 20, "not_applicable", "ratio", "volume over 20-day mean" + _UNDETERMINED_DESCRIPTION, direction=0),
        _spec("price_volume_corr_20d", "volume", _price_volume_corr_20d, ("adjusted_close", "volume"), 21, "local_adjusted", "correlation", "20-day price-volume correlation" + _UNDETERMINED_DESCRIPTION, direction=0),
        _spec("range_position", "range", _range_position, ("adjusted_high", "adjusted_low", "adjusted_close"), 1, "local_adjusted", "ratio", "intraday range position", direction=1),
        _spec("realized_vol_20d", "volatility", _realized_vol_20d, ("adjusted_close",), 21, "local_adjusted", "annualized_ratio", "20-day realized volatility", direction=-1),
        _spec("natr_14d", "volatility", _natr_14d, ("adjusted_high", "adjusted_low", "adjusted_close"), 14, "local_adjusted", "ratio", "14-day normalized ATR", direction=-1),
        _spec("amihud_20d", "liquidity", _amihud_20d, ("adjusted_close", "amount"), 21, "local_adjusted", "ratio_per_currency", "20-day Amihud illiquidity", direction=-1),
        _spec("overnight_gap_20d", "return", _overnight_gap_20d, ("adjusted_open", "adjusted_close"), 21, "local_adjusted", "ratio", "20-day mean overnight gap" + _UNDETERMINED_DESCRIPTION, direction=0, canonical=False),
    )
}

CANONICAL_FACTOR_IDS = frozenset(spec.name for spec in FACTOR_REGISTRY.values() if spec.canonical)


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


def factor_result(values: pd.DataFrame, window: int) -> FactorResult:
    """Return values and factor-only zero-dispersion diagnostics."""

    dispersion = ts_std(values, window)
    diagnostics = [
        {"stage": "factor", "code": "W012_zero_dispersion", "stock_id": stock_id}
        for stock_id in values
        if dispersion[stock_id].eq(0).any()
    ]
    return FactorResult(values, diagnostics)


def compute_factor(spec: FactorSpec, frames: dict[str, pd.DataFrame]) -> FactorResult:
    """Compute one registry factor on aligned in-memory frames."""

    return factor_result(spec.fn(frames), spec.lookback)
