"""Compatibility wrapper for core.crawlers.quarterly_scraper."""
from .._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.crawlers.quarterly_scraper")
