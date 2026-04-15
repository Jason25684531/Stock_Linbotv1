"""Deprecated compatibility package forwarding legacy tool imports to core."""

from __future__ import annotations

import warnings
from importlib import import_module


warnings.warn(
    "The 'tool' package is deprecated; migrate imports to 'core'.",
    DeprecationWarning,
    stacklevel=2,
)

_core = import_module("core")
__all__ = getattr(_core, "__all__", [])


def __getattr__(name: str):
    return getattr(_core, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_core)))