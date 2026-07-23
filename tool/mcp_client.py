"""Compatibility wrapper for core.mcp_client."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.mcp_client")