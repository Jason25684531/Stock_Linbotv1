from dataclasses import FrozenInstanceError

import pytest

from core.research.factors import CANONICAL_FACTOR_IDS, FACTOR_REGISTRY


CANONICAL_TWELVE = {
    "momentum_20d", "momentum_60d", "momentum_12_1", "near_high_252d",
    "reversal_5d", "vwap_gap", "volume_ratio_20d", "price_volume_corr_20d",
    "range_position", "realized_vol_20d", "natr_14d", "amihud_20d",
}


def test_registry_contains_the_canonical_twelve_plus_deprecated_compatibility_factors():
    expected = CANONICAL_TWELVE | {"return_5d", "overnight_gap_20d"}

    assert set(FACTOR_REGISTRY) == expected
    assert FACTOR_REGISTRY["volume_ratio_20d"].price_basis == "not_applicable"
    with pytest.raises(FrozenInstanceError):
        FACTOR_REGISTRY["momentum_20d"].name = "changed"


def test_canonical_registry_has_exactly_twelve_ids():
    assert CANONICAL_FACTOR_IDS == CANONICAL_TWELVE
    assert not FACTOR_REGISTRY["return_5d"].canonical
    assert not FACTOR_REGISTRY["overnight_gap_20d"].canonical


def test_direction_is_not_all_zero():
    directions = {spec.name: spec.direction for spec in FACTOR_REGISTRY.values()}

    assert set(directions.values()) != {0}
    assert directions["momentum_20d"] == 1
    assert directions["momentum_60d"] == 1
    assert directions["momentum_12_1"] == 1
    assert directions["near_high_252d"] == 1
    assert directions["reversal_5d"] == 1
    assert directions["range_position"] == 1
    assert directions["realized_vol_20d"] == -1
    assert directions["natr_14d"] == -1
    assert directions["amihud_20d"] == -1
    assert directions["vwap_gap"] == 0
    assert directions["volume_ratio_20d"] == 0
    assert directions["price_volume_corr_20d"] == 0


def test_undetermined_direction_is_documented_as_pending_research_not_neutral():
    for name in ("vwap_gap", "volume_ratio_20d", "price_volume_corr_20d", "return_5d", "overnight_gap_20d"):
        spec = FACTOR_REGISTRY[name]
        assert spec.direction == 0
        assert "undetermined" in spec.description
        assert "neutral" not in spec.description.lower()
