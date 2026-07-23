"""Compatibility wrapper for core.richmenu."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.richmenu")