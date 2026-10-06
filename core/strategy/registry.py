"""Pure in-memory strategy identity and factory registry."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import StrategyExecutor
from .lifecycle import StrategyLifecycle
from .spec import StrategySpec, _freeze

ExecutorFactory = Callable[[], StrategyExecutor]


class StrategyRegistryError(ValueError):
    """Base error for invalid registry operations."""


class DuplicateStrategyError(StrategyRegistryError):
    """Raised when a canonical ID or alias would be overwritten."""


class UnknownStrategyError(KeyError):
    """Raised when a canonical ID or alias is not registered."""


class FactoryResolutionError(StrategyRegistryError):
    """Raised when a registration has no executor factory."""


@dataclass(frozen=True)
class StrategyRegistration:
    """A pure registration record with optional executor factory."""

    spec: StrategySpec
    factory: ExecutorFactory | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        metadata = self.spec.metadata if self.metadata is None else self.metadata
        frozen = _freeze(metadata)
        if not isinstance(frozen, Mapping):
            raise StrategyRegistryError("registration metadata must be a mapping")
        object.__setattr__(self, "metadata", frozen)

    @property
    def canonical_id(self) -> str:
        return self.spec.strategy_id

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.spec.legacy_aliases


class StrategyRegistry:
    """Deterministic, non-persistent registry for strategy definitions."""

    def __init__(
        self, registrations: Iterable[StrategyRegistration] = ()
    ) -> None:
        self._records: dict[str, StrategyRegistration] = {}
        self._aliases: dict[str, str] = {}
        for registration in registrations:
            self.register_record(registration)

    def register(
        self,
        spec: StrategySpec,
        factory: ExecutorFactory | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategyRegistration:
        """Register one canonical spec without activation side effects."""

        return self.register_record(
            StrategyRegistration(spec=spec, factory=factory, metadata=metadata)
        )

    def register_record(
        self, registration: StrategyRegistration
    ) -> StrategyRegistration:
        canonical_id = registration.canonical_id
        if canonical_id in self._records or canonical_id in self._aliases:
            raise DuplicateStrategyError(
                f"strategy ID is already registered: {canonical_id}"
            )
        for alias in registration.aliases:
            if alias in self._records or alias in self._aliases:
                raise DuplicateStrategyError(
                    f"strategy alias is already registered: {alias}"
                )

        self._records[canonical_id] = registration
        for alias in registration.aliases:
            self._aliases[alias] = canonical_id
        return registration

    def resolve(self, identifier: str) -> str:
        """Resolve a canonical ID or alias to its canonical ID."""

        if identifier in self._records:
            return identifier
        if identifier in self._aliases:
            return self._aliases[identifier]
        raise UnknownStrategyError(identifier)

    def get(self, identifier: str) -> StrategyRegistration:
        """Return the registration resolved from a canonical ID or alias."""

        return self._records[self.resolve(identifier)]

    def get_spec(self, identifier: str) -> StrategySpec:
        return self.get(identifier).spec

    def resolve_factory(self, identifier: str) -> ExecutorFactory:
        factory = self.get(identifier).factory
        if factory is None:
            raise FactoryResolutionError(
                f"no executor factory registered for {self.resolve(identifier)}"
            )
        return factory

    def canonical_ids(self, *, include_retired: bool = True) -> tuple[str, ...]:
        if include_retired:
            return tuple(self._records)
        return self.visible_ids()

    def visible_ids(
        self,
        lifecycles: Iterable[StrategyLifecycle] | None = None,
    ) -> tuple[str, ...]:
        if lifecycles is None:
            allowed = frozenset(
                lifecycle
                for lifecycle in StrategyLifecycle
                if lifecycle is not StrategyLifecycle.RETIRED
            )
        else:
            allowed = frozenset(
                lifecycle
                if isinstance(lifecycle, StrategyLifecycle)
                else StrategyLifecycle(str(lifecycle).strip().upper())
                for lifecycle in lifecycles
            )
        return tuple(
            strategy_id
            for strategy_id, registration in self._records.items()
            if registration.spec.lifecycle in allowed
        )

    def is_visible(self, identifier: str) -> bool:
        registration = self.get(identifier)
        return registration.spec.lifecycle is not StrategyLifecycle.RETIRED

    def metadata(self, identifier: str) -> Mapping[str, Any]:
        return self.get(identifier).metadata

    def __contains__(self, identifier: object) -> bool:
        return isinstance(identifier, str) and (
            identifier in self._records or identifier in self._aliases
        )


__all__ = [
    "DuplicateStrategyError",
    "ExecutorFactory",
    "FactoryResolutionError",
    "StrategyRegistration",
    "StrategyRegistry",
    "StrategyRegistryError",
    "UnknownStrategyError",
]
