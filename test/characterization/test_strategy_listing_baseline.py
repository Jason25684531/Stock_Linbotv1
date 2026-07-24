"""Characterize the pre-change public strategy-listing contract.

Kept as diff evidence for Phase 1.1 (canonical-only listing); the 14-key
assertion is expected to fail post-change, per tasks.md 1.1.
"""

import pytest

from core.backtest.runner import get_registered_strategy_names
from core.strategy_manager import StrategyManager


@pytest.mark.xfail(
    reason="Phase 1.1 made list_strategies() canonical-only (7, not 14); "
    "this pre-change baseline is retained as diff evidence, not regression.",
    strict=True,
)
def test_current_listing_and_legacy_resolution_baseline():
    manager = StrategyManager()
    canonical = list(manager.CANONICAL_REGISTRY)
    legacy = [
        legacy_id
        for metadata in manager.STRATEGY_METADATA.values()
        for legacy_id in metadata.legacy_ids
    ]

    assert manager.list_strategies() == canonical + list(manager.LEGACY_STRATEGY_REGISTRY)
    assert len(manager.list_strategies()) == 14
    assert get_registered_strategy_names(manager) == manager.list_strategies()
    assert {legacy_id: manager.resolve(legacy_id) for legacy_id in legacy} == {
        legacy_id: canonical_id
        for canonical_id, metadata in manager.STRATEGY_METADATA.items()
        for legacy_id in metadata.legacy_ids
    }
