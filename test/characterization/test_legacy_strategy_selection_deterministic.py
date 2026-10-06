"""Deterministic pre-platform selection evidence for all legacy strategies."""

from __future__ import annotations

import pytest

from core.strategy_manager import StrategyManager
from fixtures.legacy_strategy_platform import (
    EXPECTED_SELECTIONS,
    RUNTIME_OVERRIDES,
    frozen_legacy_frame,
)


@pytest.mark.parametrize("canonical_id", tuple(EXPECTED_SELECTIONS))
def test_canonical_and_legacy_aliases_preserve_frozen_candidate_order(
    monkeypatch: pytest.MonkeyPatch, canonical_id: str
) -> None:
    """Exercise the existing manager and concrete strategy, not C1 contracts."""

    monkeypatch.setenv("FORCE_BULL_MARKET", "true")
    StrategyManager._instance = None
    manager = StrategyManager()
    expected = EXPECTED_SELECTIONS[canonical_id]
    metadata = manager.STRATEGY_METADATA[canonical_id]

    for identifier in (canonical_id, *metadata.legacy_ids):
        strategy = manager._get_or_load_strategy(identifier)
        assert strategy is not None
        strategy.set_runtime_overrides(RUNTIME_OVERRIDES)
        result = strategy.filter_candidates(frozen_legacy_frame())
        assert tuple(result["stock_id"]) == expected
        assert manager.resolve(identifier, warn_legacy=False) == canonical_id
        assert not {"score", "rank", "selected", "target_weight", "reason"} & set(
            result.columns
        )
