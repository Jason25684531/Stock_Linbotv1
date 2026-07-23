"""Compatibility wrapper for core.line_message_builder."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.line_message_builder")