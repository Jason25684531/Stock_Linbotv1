import json

import numpy as np
import pandas as pd
import pytest

from core.research import fundamental_strategy_validation as f


def _pool():
    return {
        "dataset_version": f.DATASET_VERSION,
        "universe_hash": f.TARGET_TICKER_SHA256,
        "accepted_factors": [
            {"factor_id": "G2_OPERATING_INCOME_YOY", "dataset_version": f.DATASET_VERSION, "definition": {"source_metric": "operating_income_yoy", "direction": 1, "primary_horizon": 20}, "validation_result": {"verdict": "ACCEPT"}},
            {"factor_id": "G3_EPS_YOY", "dataset_version": f.DATASET_VERSION, "definition": {"source_metric": "eps_yoy", "direction": 1, "primary_horizon": 20}, "validation_result": {"verdict": "ACCEPT"}},
        ],
    }


def _targets():
    dates = pd.to_datetime(["2023-01-02", "2023-01-09"])
    return pd.DataFrame({"signal_date": dates, "asof_date": dates, "execution_date": pd.to_datetime(["2023-01-03", "2023-01-10"]), "asset_id": ["A", "B"], "target_weight": [1.0, 0.0]})


def test_only_accepted_factors_revalidate_and_rejected_factor_blocks():
    assert f.revalidate_accepted_factor_pool(_pool())["status"] == "PASS"
    bad = _pool()
    bad["accepted_factors"][0]["validation_result"]["verdict"] = "REJECT"
    with pytest.raises(f.ContractError):
        f.revalidate_accepted_factor_pool(bad)


def test_tie_break_signal_order_and_removed_target_zero():
    scores = pd.DataFrame({"asof_date": ["2023-01-02"] * 3, "asset_id": ["B", "A", "C"], "score": [1.0, 1.0, 0.1]})
    universe = scores.assign(execution_date=pd.Timestamp("2023-01-03"), member=True, is_tradable_t1=True)
    target = f.build_target_weights(scores, universe, strategy_id="T", top_n=1, rebalance_days=1, portfolio_weighting="EQUAL")
    assert target.iloc[0]["asset_id"] == "A"
    with pytest.raises(f.ContractError):
        f.validate_targets(target.assign(execution_date=target["asof_date"]))


def test_missing_execution_price_is_deferred_without_forward_fill():
    targets = _targets()
    prices = pd.DataFrame({"A": [10.0, np.nan], "B": [10.0, 10.0]}, index=pd.to_datetime(["2023-01-03", "2023-01-10"]))
    result = f.simulate_target_weights(targets, prices)
    log = result["execution_log"]
    assert (log["reason"] == "MISSING_EXECUTION_PRICE").any()
    assert not any(pd.isna(trade["price"]) for trade in result["trades"])


def test_cost_tracks_are_monotonic_and_metrics_are_finite():
    dates = pd.bdate_range("2023-01-02", periods=70)
    scores = pd.DataFrame([(date, asset, float(index)) for date in dates for index, asset in enumerate(["A", "B"])], columns=["asof_date", "asset_id", "score"])
    universe = scores.assign(execution_date=scores["asof_date"] + pd.Timedelta(days=1), member=True, is_tradable_t1=True)
    target = f.build_target_weights(scores, universe, strategy_id="T", top_n=1, rebalance_days=20, portfolio_weighting="EQUAL")
    prices = pd.DataFrame({"A": np.linspace(10, 20, len(dates)), "B": np.linspace(20, 10, len(dates))}, index=dates + pd.Timedelta(days=1))
    tracks = f.run_cost_tracks(target, prices)
    metrics = {key: f.performance_metrics(value) for key, value in tracks.items()}
    assert metrics["STRESS_COST"]["total_return"] <= metrics["BASE_COST"]["total_return"] <= metrics["GROSS"]["total_return"]
    assert metrics["STRESS_COST"]["finite_returns"]


def test_temporal_folds_and_parameter_neighborhood_are_chronological():
    dates = pd.date_range("2023-01-01", periods=12, freq="MS")
    table, summary = f.temporal_stability(pd.Series(np.linspace(-.01, .02, len(dates)), index=dates), [{"fold_id": "early", "start": "2023-01-01", "end": "2023-06-30"}, {"fold_id": "late", "start": "2023-07-01", "end": "2023-12-31"}])
    assert table.iloc[0]["fold_id"] == "early"
    assert summary["status"] == "PASS"
    board = pd.DataFrame([{"strategy_id": "a", "top_n": 5, "rebalance_days": 20, "portfolio_weighting": "EQUAL", "cagr": .1}, {"strategy_id": "b", "top_n": 10, "rebalance_days": 20, "portfolio_weighting": "EQUAL", "cagr": .2}, {"strategy_id": "c", "top_n": 5, "rebalance_days": 60, "portfolio_weighting": "EQUAL", "cagr": .1}])
    result = f.parameter_stability(board, "a")
    assert result["neighbor_count"] == 2


def test_bootstrap_and_fresh_oos_boundary_are_deterministic():
    returns = pd.Series(np.linspace(-.01, .02, 50))
    assert f.moving_block_bootstrap(returns, draws=10, block_length=3) == f.moving_block_bootstrap(returns, draws=10, block_length=3)
    available = pd.to_datetime(["2026-07-28", "2026-07-29", "2026-08-03"])
    report = f.fresh_oos_availability(available, rebalance_days=20, minimum={"trading_days": 3, "rebalances": 1, "months": 1})
    assert report["fresh_start"] == "2026-07-29"
    assert report["status"] == "INSUFFICIENT_DATA"


def test_frozen_spec_mutation_invalidates_previous_candidate(tmp_path):
    board = pd.DataFrame([{"strategy_id": "a", "strategy_valid": True, "ranking_score": 1.0, "top_n": 5}])
    path = tmp_path / "FrozenStrategySpec.json"
    first = f.shortlist_and_freeze(board, path)
    assert first["strategy_fingerprint"]
    changed = board.assign(top_n=10)
    with pytest.raises(f.ContractError):
        f.shortlist_and_freeze(changed, path)


def test_engine_parity_uses_strict_tolerance_and_reports_missing_peer():
    assert f.engine_parity({}, None)["status"] == "NOT_APPLICABLE"
    snapshot = {key: [1.0, 2.0] for key in ("selection", "orders", "positions", "cash", "daily_return", "daily_equity", "final_equity")}
    assert f.engine_parity(snapshot, snapshot)["status"] == "PASS"


def test_fresh_oos_fail_is_honest_and_does_not_rewrite_candidate():
    availability = {"status": "PASS"}
    verdict = f.fresh_oos_verdict(availability, {"finite_returns": True, "trade_count": 1, "total_return": -0.1, "mdd": -0.2}, frozen_fingerprint="fp", observed_fingerprint="fp")
    assert verdict["status"] == "FAIL"
