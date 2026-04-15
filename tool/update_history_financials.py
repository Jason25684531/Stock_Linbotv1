"""Compatibility wrapper for core.update_history_financials."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.update_history_financials")