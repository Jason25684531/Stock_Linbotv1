from __future__ import annotations

import pytest

from core.strategy.adapters.legacy import build_legacy_registry
from core.strategy.registry import DuplicateStrategyError, UnknownStrategyError
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


def test_registry_has_exactly_seven_canonical_ids_and_fourteen_aliases() -> None:
    registry = build_legacy_registry()
    assert registry.canonical_ids() == CANONICAL_IDS
    assert tuple(registry.get(alias).canonical_id for alias in ALIASES) == tuple(
        ALIASES.values()
    )
    assert tuple(registry.canonical_ids()) == tuple(StrategyManager.CANONICAL_REGISTRY)
    assert all(
        registry.get(alias).spec is registry.get(canonical).spec
        for alias, canonical in ALIASES.items()
    )


def test_registry_factories_are_lazy_and_resolve_aliases_to_same_adapter_type() -> None:
    registry = build_legacy_registry()
    factory = registry.resolve_factory("v34_turbo")
    assert registry.get("v34_turbo").factory is factory
    first = factory()
    second = registry.resolve_factory("growth_momentum_breakout")()
    assert type(first) is type(second)
    assert first is not second


def test_registry_rejects_duplicate_and_unknown_identity() -> None:
    registry = build_legacy_registry()
    with pytest.raises(DuplicateStrategyError):
        registry.register(registry.get_spec("hybrid_trend_rank"))
    with pytest.raises(UnknownStrategyError):
        registry.resolve("missing")


def test_manager_platform_registry_is_opt_in_and_does_not_change_legacy_listing() -> None:
    StrategyManager._instance = None
    manager = StrategyManager()
    before = tuple(manager.list_strategies())
    platform_registry = manager.get_platform_registry()
    assert tuple(manager.list_strategies()) == before == CANONICAL_IDS
    assert platform_registry.canonical_ids() == CANONICAL_IDS
    assert manager.get_platform_registry() is platform_registry
