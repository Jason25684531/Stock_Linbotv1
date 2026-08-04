from dataclasses import FrozenInstanceError

import pytest

from core.research.factors import FACTOR_REGISTRY


def test_registry_has_the_approved_immutable_first_batch():
    expected = {
        "momentum_20d", "momentum_60d", "momentum_12_1", "near_high_252d",
        "return_5d", "volume_ratio_20d", "price_volume_corr_20d", "range_position",
        "realized_vol_20d", "natr_14d", "amihud_20d", "overnight_gap_20d",
    }

    assert set(FACTOR_REGISTRY) == expected
    assert all(spec.direction == 0 for spec in FACTOR_REGISTRY.values())
    assert FACTOR_REGISTRY["volume_ratio_20d"].price_basis == "not_applicable"
    with pytest.raises(FrozenInstanceError):
        FACTOR_REGISTRY["momentum_20d"].name = "changed"
