"""C1 identity and Fundamental isolation characterization evidence."""

import warnings

import pytest

from core.strategy_manager import StrategyManager


CANONICAL_IDS = (
    "hybrid_trend_rank",
    "defensive_low_volatility",
    "growth_momentum_breakout",
    "quality_growth",
    "institutional_flow_confirmation",
    "mean_reversion",
    "quality_value_low_volatility",
)

ALIASES = {
    "v31": "hybrid_trend_rank",
    "v31_hybrid": "hybrid_trend_rank",
    "v33": "defensive_low_volatility",
    "v33_low_vol": "defensive_low_volatility",
    "v34": "growth_momentum_breakout",
    "v34_turbo": "growth_momentum_breakout",
    "v35": "quality_growth",
    "v35_innovation": "quality_growth",
    "v36": "institutional_flow_confirmation",
    "v36_chip_momentum": "institutional_flow_confirmation",
    "v37": "mean_reversion",
    "v37_mean_reversion": "mean_reversion",
    "v38": "quality_value_low_volatility",
    "v38_value_dividend": "quality_value_low_volatility",
}


def test_exact_canonical_and_alias_identity_contract() -> None:
    manager = StrategyManager()
    assert tuple(manager.list_strategies()) == CANONICAL_IDS
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        resolved = {alias: manager.resolve(alias) for alias in ALIASES}
    assert resolved == ALIASES
    assert len(captured) == len(ALIASES)
    with pytest.raises(KeyError):
        manager.resolve("not_a_strategy")


def test_fundamental_runtime_registration_stays_outside_legacy_listing() -> None:
    manager = StrategyManager()
    registration = manager.runtime_strategy_registrations()[
        manager.FUNDAMENTAL_RUNTIME_STRATEGY_ID
    ]
    assert manager.FUNDAMENTAL_RUNTIME_STRATEGY_ID not in manager.list_strategies()
    assert registration["strategy_id"] == manager.FUNDAMENTAL_RUNTIME_STRATEGY_ID
    assert registration["strategy_fingerprint"]
    assert registration["ENABLED_FOR_LIVE"] == "NO"
    assert registration["BROKER_ORDER_SUBMISSION"] == "DISABLED"
