from __future__ import annotations

import pandas as pd

from jobs import scheduler


def test_fundamental_operation_is_once_before_run_daily():
    for name in ("daily", "evening"):
        steps = [step.target for step in scheduler.PIPELINES[name]]
        assert steps.count("fundamental_shadow_operation") == 1
        assert steps.index("fundamental_shadow_operation") < steps.index("run_daily")


def test_blocked_operation_does_not_duplicate_or_stop_default_pipeline(monkeypatch):
    calls: list[str] = []

    def fake_run_job(target, *args, **kwargs):
        calls.append(target)
        return 2 if target == "fundamental_shadow_operation" else 0

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)
    assert scheduler.run_pipeline("daily", stop_on_error=False) == 2
    assert calls.count("fundamental_shadow_operation") == 1
    assert calls.index("fundamental_shadow_operation") < calls.index("run_daily")


def test_stop_on_error_keeps_legacy_steps_after_operation_blocked(monkeypatch):
    calls: list[str] = []

    def fake_run_job(target, *args, **kwargs):
        calls.append(target)
        return 2 if target == "fundamental_shadow_operation" else 0

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)
    assert scheduler.run_pipeline("daily", stop_on_error=True) == 2
    assert calls == ["update_database", "fundamental_shadow_operation"]


def test_run_daily_fundamental_block_is_status_only(monkeypatch):
    from jobs import run_daily

    persisted = []
    monkeypatch.setattr(
        run_daily,
        "_persist_strategy_recommendations",
        lambda *args, **kwargs: persisted.append(args) or (0, False),
    )
    monkeypatch.setattr(
        "core.runtime.fundamental_production.evaluate_production",
        lambda *args, **kwargs: {
            "status": {"production_eligibility": "BLOCKED", "block_reason": "INSUFFICIENT_OOS", "recommendation_count": 0},
            "rows": pd.DataFrame(),
        },
    )

    result = run_daily.run_fundamental_production("2026-09-17", engine=object())
    assert result["status"]["block_reason"] == "INSUFFICIENT_OOS"
    assert persisted == []
