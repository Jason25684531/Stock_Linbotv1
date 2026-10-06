"""Adapters that expose existing strategy implementations to domain contracts."""

from .legacy import (
    LegacyStrategyAdapter,
    build_legacy_registry,
    build_legacy_specs,
)

__all__ = [
    "LegacyStrategyAdapter",
    "build_legacy_registry",
    "build_legacy_specs",
]
