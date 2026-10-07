"""Compatibility adapter for the repository's existing strategies."""

from __future__ import annotations

import importlib
import math
from collections.abc import Mapping
from numbers import Real
from types import MappingProxyType
from typing import Any, Protocol

from core.recommendation.contracts import Recommendation

from ..contracts import SelectionResult, StrategyContext
from ..lifecycle import StrategyLifecycle
from ..registry import StrategyRegistry
from ..spec import StrategySpec


class LegacyStrategy(Protocol):
    """Minimum legacy surface required by the adapter."""

    def filter_candidates(self, data: Any) -> Any:
        """Return the existing legacy candidate table."""


def _load_factory(path: str) -> type[Any]:
    module_name, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _clean_value(value: Any) -> Any:
    """Convert numeric NaN values to explicit absence without changing values."""

    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, Real) and not isinstance(value, bool):
        try:
            if math.isnan(float(value)):
                return None
        except (TypeError, ValueError):
            pass
    return value


def _row_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return _clean_value(row[name])
    return None


def _as_rows(result: Any) -> list[Mapping[str, Any]]:
    """Read rows from a DataFrame-like legacy result without importing pandas."""

    if not hasattr(result, "to_dict"):
        raise TypeError("legacy strategy must return a DataFrame-like result")
    rows = result.to_dict(orient="records")
    if not isinstance(rows, list):
        raise TypeError("legacy strategy result rows must be a list")
    return rows


class LegacyStrategyAdapter:
    """Adapt one existing ``filter_candidates`` implementation."""

    def __init__(self, strategy: LegacyStrategy) -> None:
        if not callable(getattr(strategy, "filter_candidates", None)):
            raise TypeError("legacy strategy must provide filter_candidates")
        self._strategy = strategy

    @property
    def strategy(self) -> LegacyStrategy:
        """Return the wrapped strategy for compatibility inspection."""

        return self._strategy

    def execute(
        self, context: StrategyContext, spec: StrategySpec
    ) -> SelectionResult:
        """Execute the legacy method and map only fields it actually exposes."""

        data = context.data
        if data is None:
            raise ValueError("legacy strategy execution requires context.data")

        runtime_overrides = context.settings.get("runtime_overrides")
        if runtime_overrides is not None:
            if not isinstance(runtime_overrides, Mapping):
                raise ValueError("runtime_overrides must be a mapping")
            set_overrides = getattr(self._strategy, "set_runtime_overrides", None)
            if callable(set_overrides):
                set_overrides(dict(runtime_overrides))

        source = data.copy() if callable(getattr(data, "copy", None)) else data
        # Deliberately do not catch exceptions: legacy failure semantics remain
        # visible to callers and parity tests.
        result = self._strategy.filter_candidates(source)
        columns = tuple(str(column) for column in getattr(result, "columns", ()))
        rows: list[Recommendation] = []
        for row in _as_rows(result):
            stock_id = _row_value(row, "stock_id")
            if stock_id is None:
                raise ValueError("legacy selection row is missing stock_id")
            asof_date = context.asof_date
            if asof_date is None:
                asof_date = _row_value(row, "asof_date", "trade_date")
            if asof_date is None:
                raise ValueError(
                    "legacy selection row is missing asof_date and trade_date"
                )
            rows.append(
                Recommendation(
                    stock_id=str(stock_id),
                    strategy_id=spec.strategy_id,
                    asof_date=asof_date,
                    score=_row_value(row, "score", "ai_score"),
                    rank=_row_value(row, "rank"),
                    selected=_row_value(row, "selected"),
                    target_weight=_row_value(row, "target_weight"),
                    reason=_row_value(row, "reason"),
                    close_price=_row_value(row, "close_price"),
                    ai_score=_row_value(row, "ai_score"),
                    rsi=_row_value(row, "rsi"),
                    volume=_row_value(row, "volume"),
                    news_boost_reason=_row_value(row, "news_boost_reason"),
                )
            )
        return SelectionResult(
            rows=tuple(rows),
            metadata={
                "legacy_result": result,
                "legacy_columns": columns,
                "strategy_id": spec.strategy_id,
            },
        )


def build_legacy_specs() -> Mapping[str, StrategySpec]:
    """Build specs from the current StrategyManager identity metadata."""

    from core.strategy_manager import StrategyManager

    specs: dict[str, StrategySpec] = {}
    for strategy_id, metadata in StrategyManager.STRATEGY_METADATA.items():
        registry_path = StrategyManager.CANONICAL_REGISTRY[strategy_id]
        specs[strategy_id] = StrategySpec(
            strategy_id=strategy_id,
            display_name=metadata.display_name_en,
            version="legacy",
            lifecycle=StrategyLifecycle.PRODUCTION,
            legacy_aliases=metadata.legacy_ids,
            metadata={
                "category": metadata.category,
                "display_name_zh": metadata.display_name_zh,
                "display_name_en": metadata.display_name_en,
                "factory_path": registry_path,
                "version_source": "not versioned by legacy implementation",
            },
        )
    return MappingProxyType(specs)


def build_legacy_registry() -> StrategyRegistry:
    """Create a fresh registry with lazy factories for all seven strategies."""

    from core.strategy_manager import StrategyManager

    specs = build_legacy_specs()
    registry = StrategyRegistry()
    for strategy_id, spec in specs.items():
        path = StrategyManager.CANONICAL_REGISTRY[strategy_id]

        def factory(path: str = path) -> LegacyStrategyAdapter:
            return LegacyStrategyAdapter(_load_factory(path)())

        registry.register(spec, factory=factory)
    return registry


__all__ = [
    "LegacyStrategyAdapter",
    "LegacyStrategy",
    "build_legacy_registry",
    "build_legacy_specs",
]
