"""Test-only proof that a new strategy family needs no caller changes."""

import ast
import inspect

from core.recommendation.contracts import Recommendation
from core.strategy import (
    SelectionResult,
    StrategyContext,
    StrategyExecutor,
    StrategyLifecycle,
    StrategyRegistry,
    StrategyRunner,
    StrategySpec,
)
from core.strategy_manager import StrategyManager


class _FixtureExecutor:
    def execute(self, context: StrategyContext, spec: StrategySpec) -> SelectionResult:
        return SelectionResult(
            rows=(
                Recommendation(
                    stock_id="2330",
                    strategy_id=spec.strategy_id,
                    asof_date=context.asof_date or "2026-10-06",
                    score=0.5,
                    rank=1,
                    selected=True,
                ),
            ),
            metadata={"fixture": "extensibility"},
        )


def test_new_strategy_can_be_added_without_runner_or_caller_change() -> None:
    production_ids = tuple(StrategyManager.CANONICAL_REGISTRY)
    registry = StrategyRegistry()
    executor: StrategyExecutor = _FixtureExecutor()
    registry.register(
        StrategySpec(
            strategy_id="fixture_new_family",
            display_name="Fixture New Family",
            version="test",
            lifecycle=StrategyLifecycle.RESEARCH,
            legacy_aliases=("fixture_alias",),
        ),
        factory=lambda: executor,
    )

    result = StrategyRunner(registry).execute(
        "fixture_alias",
        StrategyContext(asof_date="2026-10-06", data=object()),
    )

    row = result.rows[0].to_dict()
    assert row["strategy_id"] == "fixture_new_family"
    assert row["score"] == 0.5
    assert row["rank"] == 1
    assert row["selected"] is True
    assert row["target_weight"] is None
    assert row["reason"] is None
    assert tuple(StrategyManager.CANONICAL_REGISTRY) == production_ids

    source = inspect.getsource(StrategyRunner)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.If) and "strategy_id" in ast.unparse(node.test)
        for node in ast.walk(tree)
    )
