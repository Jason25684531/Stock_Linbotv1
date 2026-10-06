from __future__ import annotations

import json

from core.strategy_manager import StrategyManager


def test_legacy_alias_settings_are_resolved_without_rewriting(tmp_path) -> None:
    settings_path = tmp_path / "strategy_settings.json"
    original = {
        "version": "3.0",
        "active_strategies": ["v34_turbo"],
        "random_strategy_pool": ["v35_innovation"],
        "per_strategy_overrides": {"v34_turbo": {"threshold": 1}},
    }
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    StrategyManager._instance = None
    manager = StrategyManager(settings_path=settings_path)
    assert manager.get_active_strategy_names() == ["growth_momentum_breakout"]
    assert manager.get_random_strategy_pool() == ["v35_innovation"]
    assert json.loads(settings_path.read_text(encoding="utf-8")) == original
