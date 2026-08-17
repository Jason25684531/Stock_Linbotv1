import json

import numpy as np
import pandas as pd
import pytest


def test_frozen_handoff_uses_manifest_candidate_count_not_a_constant(tmp_path):
    from core.research.d5_handoff import load_frozen_handoff

    handoff = tmp_path / "handoff"
    handoff.mkdir()
    pd.DataFrame({"factor_id": ["a", "b"], "status": ["CANDIDATE", "CANDIDATE"]}).to_csv(handoff / "candidate_factors.csv", index=False)
    pd.DataFrame({"factor_id": ["a", "b"], "other_factor_id": ["a", "b"], "correlation": [1.0, 1.0]}).to_csv(handoff / "factor_correlation.csv", index=False)
    (handoff / "handoff_manifest.json").write_text(json.dumps({"candidate_count": 2, "candidate_factor_ids": ["a", "b"]}), encoding="utf-8")

    assert load_frozen_handoff(handoff).candidate_count == 2


def test_frozen_handoff_exposes_manifest_provenance(tmp_path):
    from core.research.d5_handoff import load_frozen_handoff

    handoff = tmp_path / "handoff"
    handoff.mkdir()
    pd.DataFrame({"factor_id": ["a"], "status": ["CANDIDATE"]}).to_csv(handoff / "candidate_factors.csv", index=False)
    pd.DataFrame({"factor_id": ["a"], "other_factor_id": ["a"], "correlation": [1.0]}).to_csv(handoff / "factor_correlation.csv", index=False)
    (handoff / "handoff_manifest.json").write_text(json.dumps({"candidate_count": 1, "candidate_factor_ids": ["a"], "handoff_id": "frozen"}), encoding="utf-8")

    assert load_frozen_handoff(handoff).handoff_id == "frozen"


def test_unknown_redundancy_share_never_increases_during_normalization():
    from core.research.composite_factor import build_factor_weights

    candidates = pd.DataFrame({"factor_id": ["known", "unknown"], "mean_ic": [2.0, 1.0], "icir": [2.0, 1.0]})
    correlation = pd.DataFrame([[1.0, 0.9], [0.9, np.nan]], index=candidates.factor_id, columns=candidates.factor_id)

    weights = build_factor_weights(candidates, correlation, "redundancy_adjusted").set_index("factor_id")

    assert weights.loc["unknown", "final_factor_weight"] == pytest.approx(weights.loc["unknown", "base_quality_weight"])
    assert weights.final_factor_weight.sum() == pytest.approx(1.0)


def test_composite_outputs_keep_frozen_handoff_provenance():
    from core.research.composite_factor import build_composite_scores, build_factor_weights

    candidates = pd.DataFrame({"factor_id": ["a"], "mean_ic": [1.0], "icir": [1.0]})
    correlation = pd.DataFrame([[1.0]], index=["a"], columns=["a"])
    weights = build_factor_weights(candidates, correlation, "equal", source_handoff_id="frozen")
    ranks = pd.DataFrame({"asof_date": ["2026-01-02"], "asset_id": ["A"], "factor_id": ["a"], "direction_adjusted_rank": [0.9]})

    scores = build_composite_scores(ranks, weights, "equal", source_handoff_id="frozen")

    assert weights.source_handoff_id.tolist() == ["frozen"]
    assert scores.source_handoff_id.tolist() == ["frozen"]


def test_target_weights_freeze_t_plus_one_execution_date():
    from core.research.target_weights import build_target_weights

    scores = pd.DataFrame({"asof_date": ["2026-01-02", "2026-01-02"], "asset_id": ["A", "B"], "composite_score": [0.9, 0.8]})
    universe = pd.DataFrame({"asof_date": ["2026-01-02", "2026-01-02"], "asset_id": ["A", "B"], "member": [True, True], "is_tradable_t1": [True, True], "execution_date": ["2026-01-05", "2026-01-05"]})

    weights = build_target_weights(scores, universe, config_id="c", top_n=2, stock_weighting="equal")

    assert weights.execution_date.tolist() == [pd.Timestamp("2026-01-05")] * 2
    assert weights.target_weight.sum() == pytest.approx(1.0)


def test_target_weights_normalizes_asof_date_merge_keys():
    from core.research.target_weights import build_target_weights

    scores = pd.DataFrame({"asof_date": pd.to_datetime(["2026-01-02"]), "asset_id": ["A"], "composite_score": [0.9]})
    universe = pd.DataFrame({"asof_date": ["2026-01-02"], "asset_id": ["A"], "member": [True], "is_tradable_t1": [True], "execution_date": ["2026-01-05"]})

    assert len(build_target_weights(scores, universe, config_id="c", top_n=1, stock_weighting="equal")) == 1


def test_score_weighted_targets_fall_back_to_equal_when_scores_are_zero():
    from core.research.target_weights import build_target_weights

    scores = pd.DataFrame({"asof_date": ["2026-01-02", "2026-01-02"], "asset_id": ["A", "B"], "composite_score": [0.0, 0.0]})
    universe = pd.DataFrame({"asof_date": ["2026-01-02", "2026-01-02"], "asset_id": ["A", "B"], "member": [True, True], "is_tradable_t1": [True, True], "execution_date": ["2026-01-05", "2026-01-05"]})

    weights = build_target_weights(scores, universe, config_id="c", top_n=2, stock_weighting="score_weighted")

    assert weights.target_weight.tolist() == [pytest.approx(0.5), pytest.approx(0.5)]


def test_rebalance_calendar_uses_actual_trading_days():
    from core.research.target_weights import build_rebalance_calendar

    days = pd.bdate_range("2026-01-01", periods=61)

    assert len(build_rebalance_calendar(pd.Series(days), 20)) == 4
    assert len(build_rebalance_calendar(pd.Series(days), 60)) == 2


def test_shortlist_zscore_has_defined_zero_std_nan_and_inf_behavior():
    from core.research.portfolio_research import safe_zscore

    result = safe_zscore(pd.Series([2.0, 2.0, np.nan, np.inf]))

    assert result.tolist() == [0.0, 0.0, 0.0, 0.0]


def test_parameter_robustness_uses_one_parameter_neighbors():
    from core.research.portfolio_research import build_parameter_robustness

    scoreboard = pd.DataFrame({"config_id": ["a", "b", "c", "d"], "combination_method": ["equal"] * 4, "top_n": [20, 30, 20, 50], "rebalance_days": [20, 20, 60, 20], "stock_weighting": ["equal"] * 4, "sharpe": [1.0, 2.0, 3.0, 4.0], "total_return": [0.1] * 4, "max_drawdown": [-0.1] * 4, "turnover": [0.1] * 4})

    result = build_parameter_robustness(scoreboard).set_index("config_id")

    assert result.loc["a", "neighbor_sharpe_mean"] == pytest.approx(2.5)
    assert result.loc["a", "neighbor_sharpe_min"] == pytest.approx(2.0)
    assert result.loc["b", "neighbor_count"] == 2


def test_shortlist_freezes_up_to_five_and_stops_below_three():
    from core.research.portfolio_research import select_shortlist

    scoreboard = pd.DataFrame({"config_id": list("abcde"), "total_return": [0.1] * 5, "max_drawdown": [-0.1] * 5, "neighbor_sharpe_min": [0.1] * 5, "sharpe": range(1, 6), "sortino": range(1, 6), "calmar": range(1, 6), "turnover": [0.2] * 5, "sharpe_drop": [0.1] * 5})

    assert len(select_shortlist(scoreboard)) == 5
    with pytest.raises(ValueError):
        select_shortlist(scoreboard.iloc[:2])


def test_portfolio_scoreboard_summary_uses_orders_for_cost_and_turnover():
    from core.research.portfolio_research import summarize_portfolio

    result = {"returns": pd.Series([0.0, 0.1]), "value": pd.Series([100.0, 110.0], index=pd.to_datetime(["2026-01-02", "2026-01-05"])), "orders": pd.DataFrame({"Size": [1.0], "Price": [10.0], "Fees": [0.5]})}

    summary = summarize_portfolio({"config_id": "c"}, result)

    assert summary["turnover"] == pytest.approx(0.1)
    assert summary["estimated_cost"] == pytest.approx(0.5)


def test_shortlist_rejects_non_finite_metrics_with_a_reason():
    from core.research.portfolio_research import shortlist_eligibility

    scoreboard = pd.DataFrame({
        "config_id": ["good", "nan", "inf"], "total_return": [0.1, 0.1, 0.1],
        "max_drawdown": [-0.1] * 3, "neighbor_sharpe_min": [0.1] * 3,
        "sharpe": [1.0, np.nan, np.inf], "sortino": [1.0] * 3,
        "calmar": [1.0] * 3, "turnover": [0.2] * 3, "sharpe_drop": [0.1] * 3,
    })

    eligible = shortlist_eligibility(scoreboard).set_index("config_id")

    assert eligible.loc["good", "shortlist_eligible"]
    assert not eligible.loc["nan", "shortlist_eligible"]
    assert eligible.loc["nan", "invalid_reason"] == "non_finite_sharpe"
    assert not eligible.loc["inf", "shortlist_eligible"]
    assert eligible.loc["inf", "invalid_reason"] == "non_finite_sharpe"


def test_vectorbt_adapter_executes_only_on_execution_date():
    from core.research.vectorbt_adapter import run_vectorbt

    close = pd.DataFrame({"A": [10.0, 11.0]}, index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    weights = pd.DataFrame({"execution_date": pd.to_datetime(["2026-01-05"]), "asset_id": ["A"], "target_weight": [1.0]})

    result = run_vectorbt(close, weights, fee_rate=0.0, tax_rate=0.0)

    assert result["orders"].iloc[0]["Timestamp"] == pd.Timestamp("2026-01-05")


def test_vectorbt_adapter_closes_assets_absent_from_the_next_target():
    from core.research.vectorbt_adapter import run_vectorbt

    close = pd.DataFrame({"A": [10.0, 10.0], "B": [10.0, 10.0]}, index=pd.to_datetime(["2026-01-05", "2026-01-06"]))
    weights = pd.DataFrame({"execution_date": pd.to_datetime(["2026-01-05", "2026-01-06"]), "asset_id": ["A", "B"], "target_weight": [1.0, 1.0]})

    result = run_vectorbt(close, weights, fee_rate=0.0, tax_rate=0.0)

    assert len(result["orders"]) == 3


def test_vectorbt_sparse_instructions_are_exactly_scheduled_dates_and_orders_are_a_subset():
    from core.research.vectorbt_adapter import run_vectorbt

    dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"])
    close = pd.DataFrame({"A": [10.0, 10.0, 11.0, 11.0], "B": [10.0, 10.0, 10.0, 10.0]}, index=dates)
    targets = pd.DataFrame({"execution_date": pd.to_datetime(["2026-01-05", "2026-01-07"]), "asset_id": ["A", "B"], "target_weight": [1.0, 1.0]})

    result = run_vectorbt(close, targets, fee_rate=0.0, tax_rate=0.0, sparse_rebalance=True)

    instruction_dates = result["instruction_matrix"].dropna(how="all").index
    assert instruction_dates.equals(result["scheduled_instruction_dates"])
    assert result["instruction_matrix"].loc[~result["instruction_matrix"].index.isin(instruction_dates)].isna().all().all()
    assert result["actual_order_dates"].isin(result["scheduled_instruction_dates"]).all()
    assert result["orders_on_non_rebalance_dates"] == 0


def test_corrected_d5_repro_requires_exact_csv_artifacts(tmp_path):
    from jobs.run_portfolio_research import verify_repro

    canonical, repro = tmp_path / "canonical", tmp_path / "repro"
    canonical.mkdir()
    repro.mkdir()
    pd.DataFrame({"value": [1.0]}).to_csv(canonical / "scoreboard.csv", index=False)
    pd.DataFrame({"value": [1.0]}).to_csv(repro / "scoreboard.csv", index=False)

    assert verify_repro(canonical, repro) == {"canonical_run_id": "canonical", "repro_run_id": "repro", "artifact_count": 1, "exact_match_count": 1, "mismatch_count": 0, "mismatching_artifacts": []}
