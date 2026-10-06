"""Small execution contracts shared by strategy implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, runtime_checkable

from core.recommendation.contracts import Recommendation

from .spec import StrategySpec

DateValue: TypeAlias = str | date | datetime
SelectionRow: TypeAlias = Recommendation


@dataclass(frozen=True)
class StrategyContext:
    """Explicit execution inputs without transport or persistence knowledge."""

    asof_date: DateValue | None = None
    universe: tuple[str, ...] = ()
    data: Any = None
    settings: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "universe", tuple(self.universe))
        object.__setattr__(
            self, "settings", MappingProxyType(dict(self.settings))
        )
        object.__setattr__(
            self, "metadata", MappingProxyType(dict(self.metadata))
        )


@dataclass(frozen=True)
class SelectionResult:
    """Typed result returned by a strategy executor."""

    rows: tuple[SelectionRow, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(
            self, "metadata", MappingProxyType(dict(self.metadata))
        )


@runtime_checkable
class StrategyExecutor(Protocol):
    """Capability for executing a spec against an explicit context."""

    def execute(
        self, context: StrategyContext, spec: StrategySpec
    ) -> SelectionResult:
        """Return selections for ``spec`` under ``context``."""


__all__ = [
    "DateValue",
    "SelectionResult",
    "SelectionRow",
    "StrategyContext",
    "StrategyExecutor",
]
