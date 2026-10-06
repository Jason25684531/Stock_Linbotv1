from __future__ import annotations

from jobs import scheduler


def test_scheduler_remains_the_single_pipeline_owner(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def run_job(target, extra_args=None, **kwargs):
        calls.append((target, bool(kwargs.get("dry_run"))))
        return 0

    monkeypatch.setattr(scheduler, "run_job", run_job)
    assert scheduler.run_pipeline("morning", stop_on_error=True, dry_run=True) == 0
    assert calls == [("push_to_line", True)]
    assert scheduler.PIPELINES["morning"] == (
        scheduler.ScheduledStep("push_to_line", ("--time", "morning")),
    )


def test_scheduler_stop_on_error_preserves_short_circuit(monkeypatch) -> None:
    calls: list[str] = []

    def run_job(target, extra_args=None, **kwargs):
        calls.append(target)
        return 17 if target == "update_database" else 0

    monkeypatch.setattr(scheduler, "run_job", run_job)
    monkeypatch.setattr(scheduler, "_record_validation_not_run", lambda *args, **kwargs: None)
    assert scheduler.run_pipeline("daily", stop_on_error=True) == 17
    assert calls == ["update_database"]
