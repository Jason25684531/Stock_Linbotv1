"""Compatibility wrapper for core.model_utils."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.model_utils")