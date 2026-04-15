"""Compatibility wrapper package for core.crawlers."""
from .._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.crawlers")
