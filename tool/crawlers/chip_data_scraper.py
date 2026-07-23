"""Compatibility wrapper for core.crawlers.chip_data_scraper."""
from .._proxy import apply_module_proxy

apply_module_proxy(globals(), "core.crawlers.chip_data_scraper")
