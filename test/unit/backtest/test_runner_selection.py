from __future__ import annotations

import pandas as pd
import pytest

from core.backtest.runner import execute_backtest_selection
from core.strategy_manager import StrategyManager
from fixtures.legacy_strategy_platform import (
    ASOF_DATE,
    EXPECTED_SELECTIONS,
    RUNTIME_OVERRIDES,
    frozen_legacy_frame,
)


@pytest.mark.parametrize("canonical_id", tuple(EXPECTED_SELECTIONS))
def test_backtest_selection_helper_matches_direct_legacy_output(canonical_id: str) -> None:
    StrategyManager._instance = None
    manager = StrategyManager()
    direct = manager.get_strategy(canonical_id)
    assert direct is not None
    direct.set_runtime_overrides(RUNTIME_OVERRIDES)
    expected = direct.filter_candidates(frozen_legacy_frame())

    actual = execute_backtest_selection(
        manager.get_strategy_runner(),
        canonical_id,
        frozen_legacy_frame(),
        ASOF_DATE,
        RUNTIME_OVERRIDES,
    )

    pd.testing.assert_frame_equal(expected, actual, check_dtype=True, check_exact=True)
