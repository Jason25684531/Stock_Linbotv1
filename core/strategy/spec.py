"""Immutable, domain-only strategy definitions."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .lifecycle import StrategyLifecycle


class StrategySpecValidationError(ValueError):
    """Raised when a strategy definition violates its identity contract."""


def _freeze(value: Any) -> Any:
    """Recursively convert common mutable containers to immutable values."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {_freeze(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, Set) and not isinstance(value, (str, bytes, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        value = {}
    frozen = _freeze(value)
    if not isinstance(frozen, Mapping):
        raise StrategySpecValidationError("mapping fields must receive a mapping")
    return frozen


def _normalize_aliases(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise StrategySpecValidationError("legacy_aliases must be a collection")
    try:
        aliases = (
            tuple(sorted(value))
            if isinstance(value, (set, frozenset))
            else tuple(value)
        )
    except TypeError as exc:
        raise StrategySpecValidationError(
            "legacy_aliases must be a collection"
        ) from exc

    normalized: list[str] = []
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip():
            raise StrategySpecValidationError("legacy aliases must be non-empty strings")
        normalized.append(alias.strip())
    if len(set(normalized)) != len(normalized):
        raise StrategySpecValidationError("legacy aliases must not contain duplicates")
    return tuple(normalized)


def _normalize_lifecycle(value: StrategyLifecycle | str) -> StrategyLifecycle:
    if isinstance(value, StrategyLifecycle):
        return value
    if isinstance(value, str):
        try:
            return StrategyLifecycle(value.strip().upper())
        except ValueError as exc:
            raise StrategySpecValidationError(
                f"invalid lifecycle: {value!r}"
            ) from exc
    raise StrategySpecValidationError("lifecycle must be a StrategyLifecycle value")


@dataclass(frozen=True)
class StrategySpec:
    """Immutable data describing a strategy without executable dependencies."""

    strategy_id: str
    display_name: str
    version: str
    lifecycle: StrategyLifecycle
    legacy_aliases: tuple[str, ...] = ()
    universe: Any = ()
    required_features: Any = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    ranking_policy: Mapping[str, Any] = field(default_factory=dict)
    portfolio_policy: Mapping[str, Any] = field(default_factory=dict)
    execution_policy: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identity = {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "version": self.version,
        }
        for field_name, value in identity.items():
            if not isinstance(value, str) or not value.strip():
                raise StrategySpecValidationError(
                    f"{field_name} must be a non-empty string"
                )
            object.__setattr__(self, field_name, value.strip())

        lifecycle = _normalize_lifecycle(self.lifecycle)
        object.__setattr__(self, "lifecycle", lifecycle)

        aliases = _normalize_aliases(self.legacy_aliases)
        if self.strategy_id in aliases:
            raise StrategySpecValidationError(
                "canonical strategy_id cannot be one of its own aliases"
            )
        object.__setattr__(self, "legacy_aliases", aliases)

        for field_name in (
            "universe",
            "required_features",
            "parameters",
            "ranking_policy",
            "portfolio_policy",
            "execution_policy",
            "metadata",
        ):
            object.__setattr__(self, field_name, _freeze(getattr(self, field_name)))

        for field_name in (
            "parameters",
            "ranking_policy",
            "portfolio_policy",
            "execution_policy",
            "metadata",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise StrategySpecValidationError(
                    f"{field_name} must be a mapping"
                )


__all__ = ["StrategySpec", "StrategySpecValidationError"]
