#!/usr/bin/env python
"""Deploy the default Rich Menu to LINE with MCP-backed postback actions.

Usage::

    python scripts/setup_rich_menu.py

Requires ``LINE_CHANNEL_ACCESS_TOKEN`` to be set in the environment (or .env file).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on the path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.richmenu import sync_default_rich_menu_from_token


def main() -> None:
    """Entry point: upload Rich Menu and print the resulting menu ID."""
    rich_menu_id = sync_default_rich_menu_from_token()
    print(f"[OK] Rich Menu 部署完成: {rich_menu_id}")


if __name__ == '__main__':
    main()
