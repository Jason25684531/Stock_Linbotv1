import math

import pandas as pd
import pytest

from core.research.factors import FACTOR_REGISTRY, compute_factor


@pytest.fixture
def frames():
    return {
        "raw_close": pd.DataFrame({"2330": [110.0]}),
        "amount": pd.DataFrame({"2330": [1000.0]}),
        "volume": pd.DataFrame({"2330": [10.0]}),
        "adjusted_close": pd.DataFrame({"2330": [999.0]}),  # deliberately different: must be ignored
    }


def test_vwap_gap_matches_the_hand_computed_formula(frames):
    value = compute_factor(FACTOR_REGISTRY["vwap_gap"], frames).values.iloc[0, 0]

    assert value == pytest.approx(110.0 / (1000.0 / 10.0) - 1)


def test_vwap_gap_never_uses_the_adjusted_close(frames):
    with_different_adjusted = {**frames, "adjusted_close": frames["adjusted_close"] * 5}

    a = compute_factor(FACTOR_REGISTRY["vwap_gap"], frames).values
    b = compute_factor(FACTOR_REGISTRY["vwap_gap"], with_different_adjusted).values

    pd.testing.assert_frame_equal(a, b)


def test_vwap_gap_is_null_on_zero_volume(frames):
    frames["volume"] = pd.DataFrame({"2330": [0.0]})

    value = compute_factor(FACTOR_REGISTRY["vwap_gap"], frames).values.iloc[0, 0]

    assert math.isnan(value)


def test_vwap_gap_is_null_when_amount_is_missing(frames):
    frames["amount"] = pd.DataFrame({"2330": [float("nan")]})

    value = compute_factor(FACTOR_REGISTRY["vwap_gap"], frames).values.iloc[0, 0]

    assert math.isnan(value)


def test_vwap_gap_price_basis_is_the_raw_unadjusted_exception():
    assert FACTOR_REGISTRY["vwap_gap"].price_basis == "raw_unadjusted"
    assert FACTOR_REGISTRY["vwap_gap"].required_columns == ("raw_close", "amount", "volume")


def test_vwap_gap_each_day_depends_only_on_that_day(frames):
    two_days = {
        "raw_close": pd.concat([frames["raw_close"], frames["raw_close"] * 2], ignore_index=True),
        "amount": pd.concat([frames["amount"], frames["amount"]], ignore_index=True),
        "volume": pd.concat([frames["volume"], frames["volume"]], ignore_index=True),
    }
    single_day = compute_factor(FACTOR_REGISTRY["vwap_gap"], frames).values.iloc[0, 0]

    result = compute_factor(FACTOR_REGISTRY["vwap_gap"], two_days).values

    assert result.iloc[0, 0] == pytest.approx(single_day)
