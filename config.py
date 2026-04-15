"""Compatibility shim for the canonical config package.

The project now treats ``config/settings.py`` as the single source of truth.
This file remains in place so direct file-based consumers can still resolve the
same exported symbols during the migration window.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_canonical_settings_module():
    settings_path = Path(__file__).resolve().parent / "config" / "settings.py"
    spec = importlib.util.spec_from_file_location(
        "_stock_linbot_config_settings",
        settings_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load canonical settings module: {settings_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_settings = _load_canonical_settings_module()

Config = _settings.Config
DEFAULT_USER_SETTINGS = _settings.DEFAULT_USER_SETTINGS
MODE_CMD_MAP = _settings.MODE_CMD_MAP
MODE_EMOJI = _settings.MODE_EMOJI
MODE_REPLY_TEMPLATE = _settings.MODE_REPLY_TEMPLATE
USER_SETTINGS_CATEGORIES = _settings.USER_SETTINGS_CATEGORIES
USER_SETTINGS_CREATE_TABLE_SQL = _settings.USER_SETTINGS_CREATE_TABLE_SQL
USER_SETTINGS_UPSERT_SQL = _settings.USER_SETTINGS_UPSERT_SQL
V34_MODE_PRESETS = _settings.V34_MODE_PRESETS
V35_MODE_PRESETS = _settings.V35_MODE_PRESETS
get_default_user_settings = _settings.get_default_user_settings
get_user_settings_dict = _settings.get_user_settings_dict

__all__ = [
    "Config",
    "DEFAULT_USER_SETTINGS",
    "MODE_CMD_MAP",
    "MODE_EMOJI",
    "MODE_REPLY_TEMPLATE",
    "USER_SETTINGS_CATEGORIES",
    "USER_SETTINGS_CREATE_TABLE_SQL",
    "USER_SETTINGS_UPSERT_SQL",
    "V34_MODE_PRESETS",
    "V35_MODE_PRESETS",
    "get_default_user_settings",
    "get_user_settings_dict",
]