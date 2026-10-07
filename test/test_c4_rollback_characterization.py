"""C4 rollback characterization: caller seams remain C3-compatible."""

import inspect

import pandas as pd

from core.backtest.runner import select_backtest_candidates
from core.strategy_manager import StrategyManager
from fixtures.legacy_strategy_platform import ASOF_DATE, RUNTIME_OVERRIDES, frozen_legacy_frame


def test_daily_and_line_direct_rollback_seams_remain_available():
    from app.line_flows import _select_random_strategy_candidates
    from jobs import run_daily

    assert inspect.signature(run_daily.run_strategy).parameters["runner"].default is None
    StrategyManager._instance = None
    manager = StrategyManager()
    strategy = manager.get_strategy("v33")
    assert strategy is not None
    frame = frozen_legacy_frame()

    direct = strategy.filter_candidates(frame.copy())
    assert isinstance(_select_random_strategy_candidates(strategy, "v33", frame, ASOF_DATE), pd.DataFrame)
    assert _select_random_strategy_candidates(strategy, "v33", frame, ASOF_DATE).equals(direct)


def test_backtest_direct_rollback_matches_platform_and_has_no_migration_hook():
    StrategyManager._instance = None
    manager = StrategyManager()
    strategy = manager.get_strategy("v33")
    assert strategy is not None
    frame = frozen_legacy_frame()
    strategy.set_runtime_overrides(RUNTIME_OVERRIDES)

    direct = strategy.filter_candidates(frame.copy())
    rollback = select_backtest_candidates(
        strategy,
        None,
        "v33",
        frame.copy(),
        ASOF_DATE,
        RUNTIME_OVERRIDES,
    )
    pd.testing.assert_frame_equal(direct, rollback, check_exact=True)

    # Rollback is routing-only: no C4 code adds schema/data migration hooks.
    assert not any(token in inspect.getsource(select_backtest_candidates) for token in ("ALTER TABLE", "CREATE TABLE"))
