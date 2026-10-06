from __future__ import annotations

from typing import Any

import pytest

from core.strategy.contracts import (
    SelectionResult,
    StrategyContext,
    StrategyExecutor,
)
from core.strategy.lifecycle import StrategyLifecycle
from core.strategy.registry import (
    DuplicateStrategyError,
    StrategyRegistry,
    UnknownStrategyError,
)
from core.strategy.spec import StrategySpec, StrategySpecValidationError


def make_spec(
    strategy_id: str = "example_strategy",
    *,
    lifecycle: StrategyLifecycle = StrategyLifecycle.SHADOW,
    aliases: tuple[str, ...] = ("legacy_example",),
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        display_name="Example Strategy",
        version="1.0.0",
        lifecycle=lifecycle,
        legacy_aliases=aliases,
        universe=["2330", "2317"],
        required_features={"close", "volume"},
        parameters={"nested": {"thresholds": [0.1, 0.2]}},
        ranking_policy={"direction": "desc"},
        portfolio_policy={"max_holdings": 3},
        execution_policy={"slippage": 0.001},
        metadata={"owner": {"team": "research"}},
    )


def test_strategy_spec_is_recursively_immutable_and_copies_inputs() -> None:
    source = {
        "nested": {"items": ["before"]},
        "list": [{"value": 1}],
    }
    spec = StrategySpec(
        strategy_id="immutable",
        display_name="Immutable",
        version="1",
        lifecycle=StrategyLifecycle.RESEARCH,
        parameters=source,
    )

    source["nested"]["items"].append("after")
    source["list"][0]["value"] = 2

    assert spec.parameters["nested"]["items"] == ("before",)
    assert spec.parameters["list"][0]["value"] == 1
    with pytest.raises(TypeError):
        spec.parameters["nested"]["items"] += ("blocked",)
    with pytest.raises(TypeError):
        spec.parameters["nested"]["items"][0] = "blocked"


def test_strategy_spec_rejects_invalid_identity_lifecycle_and_aliases() -> None:
    with pytest.raises(StrategySpecValidationError):
        make_spec(strategy_id="")
    with pytest.raises(StrategySpecValidationError):
        make_spec(aliases=("legacy_example", "legacy_example"))
    with pytest.raises(StrategySpecValidationError):
        make_spec(strategy_id="same", aliases=("same",))
    with pytest.raises(StrategySpecValidationError):
        make_spec(lifecycle="not-a-lifecycle")  # type: ignore[arg-type]


def test_lifecycle_values_are_exact_and_registration_has_no_activation_side_effect() -> None:
    assert tuple(item.value for item in StrategyLifecycle) == (
        "RESEARCH",
        "VALIDATED",
        "FROZEN",
        "SHADOW",
        "PRODUCTION",
        "RETIRED",
    )
    registry = StrategyRegistry()
    registry.register(make_spec(lifecycle=StrategyLifecycle.SHADOW))
    assert registry.visible_ids() == ("example_strategy",)
    assert registry.resolve("legacy_example") == "example_strategy"


def test_executor_protocol_is_structural_and_transport_free() -> None:
    class Executor:
        def execute(
            self, context: StrategyContext, spec: StrategySpec
        ) -> SelectionResult:
            return SelectionResult()

    assert isinstance(Executor(), StrategyExecutor)


def test_registry_rejects_collisions_resolves_factory_and_unknown_ids() -> None:
    calls: list[str] = []

    class Executor:
        def execute(
            self, context: StrategyContext, spec: StrategySpec
        ) -> SelectionResult:
            calls.append(spec.strategy_id)
            return SelectionResult()

    factory = Executor
    registry = StrategyRegistry()
    registry.register(make_spec(), factory=factory)
    assert registry.resolve_factory("legacy_example") is factory
    assert registry.get_spec("legacy_example").strategy_id == "example_strategy"

    with pytest.raises(DuplicateStrategyError):
        registry.register(make_spec())
    with pytest.raises(DuplicateStrategyError):
        registry.register(make_spec("other", aliases=("legacy_example",)))
    with pytest.raises(UnknownStrategyError):
        registry.resolve("missing")

    registry.register(
        make_spec("retired", lifecycle=StrategyLifecycle.RETIRED, aliases=())
    )
    assert registry.visible_ids() == ("example_strategy",)
    assert registry.canonical_ids() == ("example_strategy", "retired")
    assert calls == []
