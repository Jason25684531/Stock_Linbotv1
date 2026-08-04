import math
from pathlib import Path

import pandas as pd
import pytest

from core.research.factors import FACTOR_REGISTRY, compute_factor


GOLDEN_DIR = Path("test/fixtures/research/factor_golden")


def _golden_frames(name: str) -> dict[str, pd.DataFrame]:
    fixture = pd.read_csv(GOLDEN_DIR / name, parse_dates=["trade_date"])
    close = fixture.pivot(index="trade_date", columns="stock_id", values="close")
    volume = fixture.pivot(index="trade_date", columns="stock_id", values="volume")
    return {
        "adjusted_open": close - 0.5,
        "adjusted_high": close + 1.0,
        "adjusted_low": close - 1.0,
        "adjusted_close": close,
        "volume": volume,
        "amount": close * volume,
        "raw_close": close,
    }


@pytest.fixture
def frames():
    index = pd.date_range("2025-01-01", periods=260, freq="B")
    close = pd.DataFrame({"2330": [float(10 + day) for day in range(260)]}, index=index)
    volume = pd.DataFrame({"2330": [float(1000 + day) for day in range(260)]}, index=index)
    return {
        "adjusted_open": close - 0.5,
        "adjusted_high": close + 1.0,
        "adjusted_low": close - 1.0,
        "adjusted_close": close,
        "volume": volume,
        "amount": close * volume,
        "raw_close": close * 2,
    }


def test_registry_factors_match_the_specification_formulas(frames):
    close = frames["adjusted_close"]
    volume = frames["volume"]
    amount = frames["amount"]
    day = close.index[-1]
    expected = {
        "momentum_20d": close / close.shift(20) - 1,
        "momentum_60d": close / close.shift(60) - 1,
        "momentum_12_1": close.shift(21) / close.shift(252) - 1,
        "near_high_252d": close / close.rolling(252, min_periods=252).max() - 1,
        "return_5d": close / close.shift(5) - 1,
        "volume_ratio_20d": volume / volume.rolling(20, min_periods=20).mean(),
        "price_volume_corr_20d": close.pct_change(fill_method=None).rolling(20, min_periods=20).corr(volume.where(volume > 0).map(math.log).diff()),
        "range_position": (close - frames["adjusted_low"]) / (frames["adjusted_high"] - frames["adjusted_low"]),
        "realized_vol_20d": close.pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252),
        "natr_14d": pd.DataFrame({"2330": [2.0 / value for value in close["2330"]]}, index=close.index),
        "amihud_20d": (close.pct_change(fill_method=None).abs() / amount).rolling(20, min_periods=20).mean(),
        "overnight_gap_20d": ((close - 0.5) / close.shift(1) - 1).rolling(20, min_periods=20).mean(),
    }

    for name, expected_values in expected.items():
        actual = compute_factor(FACTOR_REGISTRY[name], frames).values
        assert actual.loc[day, "2330"] == pytest.approx(expected_values.loc[day, "2330"], rel=1e-9, abs=1e-12)


def test_adjustment_unavailable_stays_null_instead_of_using_raw_prices(frames):
    frames["adjusted_close"] = frames["adjusted_close"] * float("nan")

    values = compute_factor(FACTOR_REGISTRY["momentum_20d"], frames).values

    assert values["2330"].isna().all()


def test_each_factor_first_valid_value_matches_its_declared_lookback(frames):
    for spec in FACTOR_REGISTRY.values():
        values = compute_factor(spec, frames).values["2330"]
        first_valid = values.first_valid_index()

        assert first_valid == values.index[spec.lookback - 1], spec.name


def test_tail_perturbation_cannot_change_prior_factor_values(frames):
    baseline = compute_factor(FACTOR_REGISTRY["momentum_20d"], frames).values
    changed = {name: value.copy() for name, value in frames.items()}
    changed["adjusted_close"].iloc[-5:] = 1_000_000.0

    actual = compute_factor(FACTOR_REGISTRY["momentum_20d"], changed).values

    pd.testing.assert_frame_equal(actual.iloc[:-5], baseline.iloc[:-5], rtol=1e-9, atol=1e-12)


def test_zero_volume_never_emits_infinity_for_volume_factors(frames):
    frames["volume"].iloc[-1] = 0.0

    for name in ("volume_ratio_20d", "price_volume_corr_20d", "amihud_20d"):
        value = compute_factor(FACTOR_REGISTRY[name], frames).values.iloc[-1, 0]
        assert pd.isna(value), name


def test_offline_golden_fixtures_cover_the_260_day_factor_boundaries():
    linear = _golden_frames("linear_rise_260d.csv")
    constant = _golden_frames("constant_260d.csv")
    incomplete = _golden_frames("missing_zero_volume_260d.csv")

    assert all(len(frame) == 260 for frame in (linear["adjusted_close"], constant["adjusted_close"], incomplete["adjusted_close"]))
    assert compute_factor(FACTOR_REGISTRY["momentum_12_1"], linear).values.iloc[-1, 0] == pytest.approx(248 / 17 - 1)
    assert compute_factor(FACTOR_REGISTRY["realized_vol_20d"], constant).values.iloc[-1, 0] == 0
    assert pd.isna(compute_factor(FACTOR_REGISTRY["price_volume_corr_20d"], constant).values.iloc[-1, 0])
    assert pd.isna(compute_factor(FACTOR_REGISTRY["amihud_20d"], incomplete).values.iloc[-1, 0])


def test_masking_before_factor_calculation_breaks_the_historical_window(frames):
    mask = pd.DataFrame(True, index=frames["adjusted_close"].index, columns=frames["adjusted_close"].columns)
    mask.iloc[:20] = False
    full = compute_factor(FACTOR_REGISTRY["momentum_20d"], frames).values.where(mask)
    early = {name: value.where(mask) if name == "adjusted_close" else value for name, value in frames.items()}

    assert full.iloc[20, 0] == pytest.approx(2.0)
    assert pd.isna(compute_factor(FACTOR_REGISTRY["momentum_20d"], early).values.iloc[20, 0])
