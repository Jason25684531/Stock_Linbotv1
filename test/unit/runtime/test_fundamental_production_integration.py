from __future__ import annotations

import pandas as pd

from jobs import scheduler


def test_explicit_fundamental_operation_is_not_a_daily_pipeline_dependency():
    for name in ("daily", "evening"):
        steps = [step.target for step in scheduler.PIPELINES[name]]
        assert "fundamental_shadow_operation" not in steps
        assert steps.index("run_daily") < steps.index("push_to_line")


def test_blocked_explicit_operation_cannot_stop_default_pipeline(monkeypatch):
    calls: list[str] = []

    def fake_run_job(target, *args, **kwargs):
        calls.append(target)
        return 2 if target == "fundamental_shadow_operation" else 0

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)
    assert scheduler.run_pipeline("daily", stop_on_error=False) == 0
    assert "fundamental_shadow_operation" not in calls
    assert "run_daily" in calls


def test_stop_on_error_keeps_legacy_steps_independent_of_shadow_operation(monkeypatch):
    calls: list[str] = []

    def fake_run_job(target, *args, **kwargs):
        calls.append(target)
        return 2 if target == "run_daily" else 0

    monkeypatch.setattr(scheduler, "run_job", fake_run_job)
    assert scheduler.run_pipeline("daily", stop_on_error=True) == 2
    assert calls == ["update_database", "run_daily"]


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


def test_run_daily_consumes_fundamental_through_c3_adapter(monkeypatch):
    from jobs import run_daily

    calls = []

    class _Adapter:
        def evaluate_production(self, asof_date, *, write_status):
            calls.append((asof_date, write_status))
            return {
                "status": {
                    "production_eligibility": "BLOCKED",
                    "block_reason": "INSUFFICIENT_OOS",
                    "broker_submission_status": "DISABLED",
                },
                "rows": pd.DataFrame(),
                "BROKER_ORDER_SUBMISSION": "DISABLED",
            }

    monkeypatch.setattr(
        "core.runtime.fundamental_adapter.FundamentalRuntimeAdapter", _Adapter
    )

    result = run_daily.run_fundamental_production("2026-10-06", engine=object(), dry_run=True)

    assert calls == [("2026-10-06", False)]
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"
