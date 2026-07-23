"""Compatibility wrapper for core.strategies.base."""
from .._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.strategies.base")
