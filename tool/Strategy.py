"""Compatibility wrapper for core.strategy."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.strategy")