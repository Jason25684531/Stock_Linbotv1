from __future__ import annotations

from pathlib import Path


def test_existing_production_callers_do_not_import_c2_adapter() -> None:
    roots = (Path("app"), Path("jobs"), Path("core/backtest"))
    for root in roots:
        for path in root.rglob("*.py"):
            assert "core.strategy.adapters" not in path.read_text(encoding="utf-8")
