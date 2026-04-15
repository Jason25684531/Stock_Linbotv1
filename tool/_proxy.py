"""Shared helpers for legacy tool.* module proxies."""

from __future__ import annotations

from importlib import import_module
import sys
from types import ModuleType
from typing import Any


def apply_module_proxy(namespace: dict[str, Any], canonical_module: str) -> ModuleType:
    """Alias a legacy tool.* module name to its canonical core.* module."""
    legacy_module = namespace["__name__"]
    module = import_module(canonical_module)
    sys.modules[legacy_module] = module

    parent_name, _, child_name = legacy_module.rpartition(".")
    if parent_name:
        parent_module = sys.modules.get(parent_name)
        if parent_module is not None:
            setattr(parent_module, child_name, module)

    return module