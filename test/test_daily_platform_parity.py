from __future__ import annotations

import pandas as pd
import pytest

from core.strategy import SelectionResult
from core.strategy_manager import StrategyManager
from fixtures.legacy_strategy_platform import ASOF_DATE, EXPECTED_SELECTIONS, frozen_legacy_frame
from jobs import run_daily


class _Connection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def commit(self):
        return None


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def connect(self):
        return self.connection


def _persisted_records(engine: _Engine) -> list[dict]:
    return [
        params
        for statement, params in engine.connection.executed
        if "INSERT INTO daily_recommendations" in statement
    ]


@pytest.mark.parametrize("canonical_id", tuple(EXPECTED_SELECTIONS))
def test_daily_direct_and_runner_selection_persist_identical_rows(
    monkeypatch, canonical_id: str
) -> None:
    monkeypatch.setattr(run_daily.Config, "NEWS_BOOST_ENABLED", False)
    monkeypatch.setattr(run_daily, "load_strategy_model", lambda _name: (None, None))
    StrategyManager._instance = None
    manager = StrategyManager()
    strategy = manager.get_strategy(canonical_id)
    assert strategy is not None

    direct_engine = _Engine()
    direct = run_daily.run_strategy(
        strategy, frozen_legacy_frame(), ASOF_DATE, direct_engine
    )

    platform_engine = _Engine()
    platform = run_daily.run_strategy(
        strategy,
        frozen_legacy_frame(),
        ASOF_DATE,
        platform_engine,
        runner=manager.get_strategy_runner(),
    )

    pd.testing.assert_frame_equal(direct, platform, check_dtype=True, check_exact=True)
    assert _persisted_records(platform_engine) == _persisted_records(direct_engine)


def test_daily_runner_empty_selection_preserves_heartbeat_and_dry_run() -> None:
    class _Strategy:
        name = "quality_value_low_volatility"
        display_name = "V38"
        target_return = 10
        look_ahead_days = 20
        features = ()

    class _Runner:
        def execute(self, _strategy_id, _context):
            return SelectionResult(metadata={"legacy_result": pd.DataFrame()})

    engine = _Engine()
    result = run_daily.run_strategy(
        _Strategy(),
        frozen_legacy_frame(),
        ASOF_DATE,
        engine,
        dry_run=True,
        runner=_Runner(),
    )

    assert result.empty
    assert _persisted_records(engine) == []


def test_daily_persistence_normalizer_preserves_legacy_fields_without_inventing_values() -> None:
    record = run_daily._normalize_recommendation_for_persistence(
        pd.Series(
            {
                "stock_id": "2330",
                "close_price": 952.0,
                "rsi": 59.0,
                "volume": 200_000,
            }
        ),
        "v31_hybrid",
        ASOF_DATE,
    )

    assert record == {
        "stock_id": "2330",
        "date": ASOF_DATE,
        "strategy": "v31_hybrid",
        "price": 952.0,
        "score": None,
        "rsi": 59.0,
        "volume": 200_000,
        "reason": None,
    }
    assert not {"rank", "selected", "target_weight"} & set(record)
