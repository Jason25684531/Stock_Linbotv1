"""Domain strategy contracts with a lazy legacy compatibility bridge.

The repository already contains ``core/strategy.py`` as a public V30/V31
compatibility module. Python gives a same-named package precedence over that
module, so legacy names are loaded lazily from the original file only when an
old caller requests them. Domain modules remain transport/database free.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .contracts import (
    DateValue,
    SelectionResult,
    SelectionRow,
    StrategyContext,
    StrategyExecutor,
)
from .lifecycle import StrategyLifecycle
from .registry import (
    DuplicateStrategyError,
    ExecutorFactory,
    FactoryResolutionError,
    StrategyRegistration,
    StrategyRegistry,
    StrategyRegistryError,
    UnknownStrategyError,
)
from .runner import StrategyRunner
from .spec import StrategySpec, StrategySpecValidationError

_LEGACY_MODULE: ModuleType | None = None
_LEGACY_MODULE_NAME = "core._legacy_strategy_compat"


def _load_legacy_module() -> ModuleType:
    global _LEGACY_MODULE
    if _LEGACY_MODULE is not None:
        return _LEGACY_MODULE

    legacy_path = Path(__file__).resolve().parent.parent / "strategy.py"
    module_spec = importlib.util.spec_from_file_location(
        _LEGACY_MODULE_NAME, legacy_path
    )
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load legacy strategy module: {legacy_path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[_LEGACY_MODULE_NAME] = module
    module_spec.loader.exec_module(module)
    _LEGACY_MODULE = module
    return module


def __getattr__(name: str) -> Any:
    """Resolve old V30/V31 names without loading them for domain imports."""

    legacy = _load_legacy_module()
    try:
        return getattr(legacy, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(vars(_load_legacy_module())))


__all__ = [
    "DateValue",
    "DuplicateStrategyError",
    "ExecutorFactory",
    "FactoryResolutionError",
    "SelectionResult",
    "SelectionRow",
    "StrategyContext",
    "StrategyExecutor",
    "StrategyLifecycle",
    "StrategyRegistration",
    "StrategyRegistry",
    "StrategyRegistryError",
    "StrategyRunner",
    "StrategySpec",
    "StrategySpecValidationError",
    "UnknownStrategyError",
]
