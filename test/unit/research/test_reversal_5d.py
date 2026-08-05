import pandas as pd
import pytest

from core.research.factors import FACTOR_REGISTRY, compute_factor


@pytest.fixture
def frames():
    index = pd.date_range("2025-01-01", periods=10, freq="B")
    close = pd.DataFrame({"2330": [100.0, 100.0, 100.0, 100.0, 100.0, 95.0, 95.0, 95.0, 95.0, 95.0]}, index=index)
    return {"adjusted_close": close}


def test_reversal_5d_is_the_negation_of_the_five_day_return(frames):
    reversal = compute_factor(FACTOR_REGISTRY["reversal_5d"], frames).values
    raw_return = compute_factor(FACTOR_REGISTRY["return_5d"], frames).values

    pd.testing.assert_frame_equal(reversal, -raw_return)


def test_reversal_5d_is_positive_after_a_five_day_decline(frames):
    reversal = compute_factor(FACTOR_REGISTRY["reversal_5d"], frames).values

    assert reversal["2330"].iloc[-1] == pytest.approx(0.05)


def test_reversal_5d_is_not_canonical_for_return_5d_but_registered_as_canonical_itself():
    assert FACTOR_REGISTRY["reversal_5d"].canonical
    assert not FACTOR_REGISTRY["return_5d"].canonical


def test_reversal_5d_does_not_use_future_data(frames):
    baseline = compute_factor(FACTOR_REGISTRY["reversal_5d"], frames).values
    perturbed = {"adjusted_close": frames["adjusted_close"].copy()}
    perturbed["adjusted_close"].iloc[-1] = 1_000_000.0

    result = compute_factor(FACTOR_REGISTRY["reversal_5d"], perturbed).values

    pd.testing.assert_frame_equal(result.iloc[:-1], baseline.iloc[:-1])
