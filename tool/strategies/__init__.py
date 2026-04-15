"""Compatibility wrapper package for core.strategies."""
from .._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.strategies")
