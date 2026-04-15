"""Compatibility wrapper for core.strategies.v33_low_vol."""
from .._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.strategies.v33_low_vol")
