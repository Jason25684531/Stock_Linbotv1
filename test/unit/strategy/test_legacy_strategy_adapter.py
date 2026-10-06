from __future__ import annotations

import pandas as pd
import pytest

from core.strategy.adapters.legacy import (
    LegacyStrategyAdapter,
    build_legacy_registry,
)
from core.strategy.contracts import StrategyContext, StrategyExecutor
from core.strategy_manager import StrategyManager
from fixtures.legacy_strategy_platform import (
    ASOF_DATE,
    EXPECTED_SELECTIONS,
    RUNTIME_OVERRIDES,
    frozen_legacy_frame,
)


def _context() -> StrategyContext:
    return StrategyContext(asof_date=ASOF_DATE, data=frozen_legacy_frame())


def test_adapter_satisfies_executor_and_preserves_legacy_output() -> None:
    registry = build_legacy_registry()
    for strategy_id, expected in EXPECTED_SELECTIONS.items():
        spec = registry.get_spec(strategy_id)
        adapter = registry.resolve_factory(strategy_id)()
        adapter.strategy.set_runtime_overrides(RUNTIME_OVERRIDES)

        assert isinstance(adapter, StrategyExecutor)
        result = adapter.execute(_context(), spec)

        assert tuple(row.stock_id for row in result.rows) == expected
        assert result.metadata["legacy_columns"] == tuple(
            frozen_legacy_frame().columns
        )
        assert {
            "score",
            "rank",
            "selected",
            "target_weight",
            "reason",
        }.isdisjoint(result.metadata["legacy_columns"])


@pytest.mark.parametrize(
    ("identifier", "canonical_id"),
    tuple(
        (identifier, canonical_id)
        for canonical_id in EXPECTED_SELECTIONS
        for identifier in (
            canonical_id,
            *StrategyManager.STRATEGY_METADATA[canonical_id].legacy_ids,
        )
    ),
)
def test_adapter_matches_direct_legacy_candidate_order(
    identifier: str, canonical_id: str
) -> None:
    StrategyManager._instance = None
    manager = StrategyManager()
    direct = manager.get_strategy(identifier)
    assert direct is not None
    direct.set_runtime_overrides(RUNTIME_OVERRIDES)
    direct_result = direct.filter_candidates(frozen_legacy_frame())

    registry = build_legacy_registry()
    adapter = registry.resolve_factory(identifier)()
    adapter.strategy.set_runtime_overrides(RUNTIME_OVERRIDES)
    result = adapter.execute(_context(), registry.get_spec(identifier))

    assert tuple(direct_result["stock_id"]) == tuple(
        row.stock_id for row in result.rows
    )
    assert result.metadata["strategy_id"] == canonical_id


def test_adapter_keeps_missing_semantics_absent() -> None:
    class Legacy:
        def filter_candidates(self, data: pd.DataFrame) -> pd.DataFrame:
            return data

    spec = build_legacy_registry().get_spec("hybrid_trend_rank")
    result = LegacyStrategyAdapter(Legacy()).execute(
        StrategyContext(
            asof_date=ASOF_DATE,
            data=pd.DataFrame(
                [{"stock_id": "2330", "trade_date": ASOF_DATE, "close_price": 100.0}]
            ),
        ),
        spec,
    )
    row = result.rows[0]
    assert row.stock_id == "2330"
    assert row.score is None
    assert row.rank is None
    assert row.selected is None
    assert row.target_weight is None
    assert row.reason is None
    assert row.close_price == 100.0


def test_adapter_does_not_swallow_legacy_exceptions() -> None:
    class Legacy:
        def filter_candidates(self, data: pd.DataFrame) -> pd.DataFrame:
            raise KeyError("required_column")

    spec = build_legacy_registry().get_spec("hybrid_trend_rank")
    with pytest.raises(KeyError, match="required_column"):
        LegacyStrategyAdapter(Legacy()).execute(_context(), spec)


def test_adapter_requires_explicit_execution_data() -> None:
    class Legacy:
        def filter_candidates(self, data: pd.DataFrame) -> pd.DataFrame:
            return data

    spec = build_legacy_registry().get_spec("hybrid_trend_rank")
    with pytest.raises(ValueError, match="context.data"):
        LegacyStrategyAdapter(Legacy()).execute(
            StrategyContext(), spec
        )
