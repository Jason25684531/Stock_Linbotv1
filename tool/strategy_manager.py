"""Compatibility wrapper for core.strategy_manager."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.strategy_manager")