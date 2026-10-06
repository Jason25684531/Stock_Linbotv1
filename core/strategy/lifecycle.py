"""Lifecycle values for domain strategy definitions."""

from enum import Enum


class StrategyLifecycle(str, Enum):
    """State of a strategy definition, independent from activation."""

    RESEARCH = "RESEARCH"
    VALIDATED = "VALIDATED"
    FROZEN = "FROZEN"
    SHADOW = "SHADOW"
    PRODUCTION = "PRODUCTION"
    RETIRED = "RETIRED"

__all__ = ["StrategyLifecycle"]
