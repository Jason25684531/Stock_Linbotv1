"""Application-neutral strategy resolution and executor dispatch."""

from __future__ import annotations

from .contracts import SelectionResult, StrategyContext
from .registry import StrategyRegistry
from .spec import StrategySpec


class StrategyRunner:
    """Resolve a registered identity and execute its registered executor.

    The runner deliberately owns no persistence, presentation, scheduler, or
    strategy-family policy.  Registrations provide all polymorphism.
    """

    def __init__(self, registry: StrategyRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> StrategyRegistry:
        """Return the explicit registry used for resolution."""

        return self._registry

    def resolve_spec(self, strategy_id: str) -> StrategySpec:
        """Resolve a canonical identity or supported alias to its spec."""

        return self._registry.get_spec(strategy_id)

    def execute(
        self, strategy_id: str, context: StrategyContext
    ) -> SelectionResult:
        """Dispatch the registration's executor with its resolved spec."""

        registration = self._registry.get(strategy_id)
        executor = self._registry.resolve_factory(strategy_id)()
        return executor.execute(context, registration.spec)


__all__ = ["StrategyRunner"]
