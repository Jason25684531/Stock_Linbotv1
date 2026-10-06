"""Daily orchestration characterization with frozen source-boundary inputs."""

from __future__ import annotations

import pandas as pd

from core.strategy_manager import StrategyManager
from jobs import run_daily
from fixtures.legacy_strategy_platform import (
    ASOF_DATE,
    EXPECTED_SELECTIONS,
    RUNTIME_OVERRIDES,
    frozen_legacy_frame,
)


def test_daily_dry_run_preserves_frozen_strategy_outputs(monkeypatch) -> None:
    """Keep DB/data-source seams fixed while retaining daily orchestration."""

    monkeypatch.setenv("FORCE_BULL_MARKET", "true")
    monkeypatch.setattr(run_daily, "get_db_engine", lambda: object())
    monkeypatch.setattr(run_daily, "compute_indicators_from_history", lambda *args, **kwargs: frozen_legacy_frame())
    monkeypatch.setattr(run_daily, "calculate_ratio_features", lambda frame: frame)
    monkeypatch.setattr(run_daily, "merge_financial_data", lambda frame, _engine: frame)
    monkeypatch.setattr(run_daily, "merge_revenue_data", lambda frame, _engine, **kwargs: frame)
    monkeypatch.setattr(run_daily, "resolve_matrix_news_sentiment", lambda _date: "neutral")
    monkeypatch.setattr(run_daily, "build_multi_factor_matrix", lambda frame, **kwargs: frame)
    monkeypatch.setattr(run_daily, "load_strategy_model", lambda _name: (None, ()))
    monkeypatch.setattr(run_daily, "run_fundamental_production", lambda *args, **kwargs: {"status": {"production_eligibility": "BLOCKED", "block_reason": "FIXTURE"}})
    monkeypatch.setattr(run_daily.Config, "NEWS_BOOST_ENABLED", False)
    monkeypatch.setattr(StrategyManager, "get_strategy_overrides", lambda self, _name: RUNTIME_OVERRIDES)

    observed: dict[str, tuple[str, ...]] = {}
    original = run_daily.run_strategy

    def capture(strategy, *args, **kwargs):
        result = original(strategy, *args, **kwargs)
        observed[strategy.name] = tuple(result["stock_id"])
        return result

    monkeypatch.setattr(run_daily, "run_strategy", capture)
    StrategyManager._instance = None
    summary = run_daily.run_daily_for_date(ASOF_DATE, dry_run=True)
    expected_by_runtime_name = {
        StrategyManager.STRATEGY_METADATA[canonical_id].legacy_ids[-1]: ids
        for canonical_id, ids in EXPECTED_SELECTIONS.items()
    }

    assert summary["strategy_errors"] == {}
    assert summary["strategy_counts"] == {
        name: len(ids) for name, ids in expected_by_runtime_name.items()
    }
    assert observed == expected_by_runtime_name
    assert summary["dry_run"] is True
    assert summary["skipped_persistence"] is True


def test_daily_persistence_shapes_rows_without_writing(monkeypatch) -> None:
    """Characterize dry-run recommendation and heartbeat row counts."""

    candidates = frozen_legacy_frame().head(1).assign(ai_score=0.75, news_boost_reason="fixture")
    count, heartbeat = run_daily._persist_strategy_recommendations(
        candidates, "hybrid_trend_rank", ASOF_DATE, object(), dry_run=True
    )
    assert (count, heartbeat) == (1, False)

    count, heartbeat = run_daily._persist_strategy_recommendations(
        pd.DataFrame(), "hybrid_trend_rank", ASOF_DATE, object(), dry_run=True
    )
    assert (count, heartbeat) == (1, True)
