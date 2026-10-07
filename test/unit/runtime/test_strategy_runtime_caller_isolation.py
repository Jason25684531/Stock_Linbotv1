from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_c3_runtime_boundary_stays_out_of_application_callers():
    caller_files = [
        ROOT / "jobs" / "scheduler.py",
        ROOT / "core" / "backtest" / "runner.py",
        *sorted((ROOT / "app").glob("*.py")),
    ]
    for path in caller_files:
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            assert "FundamentalRuntimeAdapter" not in source
            assert "StrategyRunner" not in source
            assert "core.runtime.contracts" not in source

    daily_source = (ROOT / "jobs" / "run_daily.py").read_text(encoding="utf-8")
    assert "FundamentalRuntimeAdapter" in daily_source
    assert "StrategyRunner" not in daily_source


def test_c4_manager_seam_does_not_consume_fundamental_runtime_policy():
    source = (ROOT / "core" / "strategy_manager.py").read_text(encoding="utf-8")
    assert "StrategyRunner" in source
    assert "FundamentalRuntimeAdapter" not in source
    assert "core.runtime.contracts" not in source


def test_legacy_adapter_has_no_fundamental_runtime_behavior():
    source = (ROOT / "core" / "strategy" / "adapters" / "legacy.py").read_text(encoding="utf-8")
    assert "fundamental" not in source.lower()
