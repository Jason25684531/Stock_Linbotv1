"""Canonical configuration package with backward-compatible exports."""

from .settings import (
    Config,
    DEFAULT_USER_SETTINGS,
    MODE_CMD_MAP,
    MODE_EMOJI,
    MODE_REPLY_TEMPLATE,
    USER_SETTINGS_CATEGORIES,
    USER_SETTINGS_CREATE_TABLE_SQL,
    USER_SETTINGS_UPSERT_SQL,
    V34_MODE_PRESETS,
    V35_MODE_PRESETS,
    get_default_user_settings,
    get_user_settings_dict,
)

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