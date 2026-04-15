"""Compatibility wrapper for core.news_agent."""
from ._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.news_agent")