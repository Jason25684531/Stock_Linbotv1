"""Compatibility wrapper for core.calc_indicators."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.calc_indicators")