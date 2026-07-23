"""Compatibility wrapper for core.report_helper."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.report_helper")