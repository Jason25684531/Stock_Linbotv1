"""Compatibility wrapper for core.db_helper."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.db_helper")