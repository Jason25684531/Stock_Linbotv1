from __future__ import annotations

import ast
import inspect

import pandas as pd
import pytest

from core.recommendation.contracts import Recommendation
from core.strategy import (
    FactoryResolutionError,
    SelectionResult,
    StrategyContext,
    StrategyLifecycle,
    StrategyRegistry,
    StrategyRunner,
    StrategySpec,
    UnknownStrategyError,
)
from core.strategy_manager import StrategyManager
from fixtures.legacy_strategy_platform import (
    ASOF_DATE,
    EXPECTED_SELECTIONS,
    RUNTIME_OVERRIDES,
    frozen_legacy_frame,
)


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[StrategyContext, StrategySpec]] = []

    def execute(self, context: StrategyContext, spec: StrategySpec) -> SelectionResult:
        self.calls.append((context, spec))
        return SelectionResult(
            rows=(
                Recommendation(
                    stock_id="2330",
                    strategy_id=spec.strategy_id,
                    asof_date=context.asof_date or "2026-10-06",
                ),
            ),
            metadata={"executor": "test"},
        )


def _spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="canonical",
        display_name="Canonical",
        version="test",
        lifecycle=StrategyLifecycle.PRODUCTION,
        legacy_aliases=("legacy",),
    )


def test_runner_resolves_alias_and_dispatches_registered_executor() -> None:
    registry = StrategyRegistry()
    executor = _Executor()
    registry.register(_spec(), factory=lambda: executor)
    runner = StrategyRunner(registry)
    context = StrategyContext(asof_date="2026-10-06", data=object())

    result = runner.execute("legacy", context)

    assert runner.resolve_spec("legacy").strategy_id == "canonical"
    assert result.rows[0].strategy_id == "canonical"
    assert result.metadata["executor"] == "test"
    assert executor.calls == [(context, runner.resolve_spec("canonical"))]


def test_runner_preserves_unknown_and_factory_errors() -> None:
    runner = StrategyRunner(StrategyRegistry())
    with pytest.raises(UnknownStrategyError):
        runner.execute("missing", StrategyContext())

    registry = StrategyRegistry()
    registry.register(_spec())
    with pytest.raises(FactoryResolutionError):
        StrategyRunner(registry).execute("canonical", StrategyContext())


def test_runner_has_no_strategy_id_business_branching_or_application_imports() -> None:
    module_source = inspect.getsource(__import__(StrategyRunner.__module__, fromlist=["*"]))
    tree = ast.parse(module_source)
    imports = {
        alias.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert not {"flask", "sqlalchemy", "scheduler"} & imports
    assert not any(
        "strategy_id" in ast.unparse(node.test)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
    )


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
def test_runner_matches_direct_legacy_execution_for_canonical_ids_and_aliases(
    identifier: str, canonical_id: str
) -> None:
    StrategyManager._instance = None
    manager = StrategyManager()
    direct = manager.get_strategy(identifier)
    assert direct is not None
    direct.set_runtime_overrides(RUNTIME_OVERRIDES)
    direct_result = direct.filter_candidates(frozen_legacy_frame())

    result = manager.get_strategy_runner().execute(
        identifier,
        StrategyContext(
            asof_date=ASOF_DATE,
            data=frozen_legacy_frame(),
            settings={"runtime_overrides": RUNTIME_OVERRIDES},
        ),
    )

    assert result.metadata["strategy_id"] == canonical_id
    pd.testing.assert_frame_equal(
        direct_result,
        result.metadata["legacy_result"],
        check_dtype=True,
        check_exact=True,
    )


def test_manager_runner_is_lazy_and_does_not_change_legacy_object_cache() -> None:
    StrategyManager._instance = None
    manager = StrategyManager()
    assert not hasattr(manager, "_strategy_runner")
    before = dict(manager._strategy_cache)

    runner = manager.get_strategy_runner()

    assert runner.registry is manager.get_platform_registry()
    assert manager.get_strategy_runner() is runner
    assert manager._strategy_cache == before
