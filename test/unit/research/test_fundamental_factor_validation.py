from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from core.research.forward_returns import compute_forward_returns
from core.research.fundamental_factor_validation import (
    FROZEN_CANDIDATES,
    FROZEN_FOLDS,
    PRIMARY_HORIZON,
    CandidateSpec,
    ValidationInputError,
    _gate,
    _spearman,
    incremental_composites,
    normalize_factor,
    temporal_stability,
    validate_candidate_request,
    write_frozen_candidate_spec,
)


def _spec(source_metric: str = "operating_margin", candidate_id: str = "Q1_OPERATING_MARGIN") -> CandidateSpec:
    return CandidateSpec(candidate_id, source_metric, "Quality", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, 20, (60,), 5, "fixed_calendar_year_folds", "STANDALONE")


def _summary(factor_id: str = "Q1_OPERATING_MARGIN", **changes: object) -> pd.DataFrame:
    row = {
        "factor_id": factor_id, "family": "Quality", "source_metric": "operating_margin", "reporting_basis": "STANDALONE", "direction": 1, "horizon": 20, "total_days": 200, "valid_ic_days": 180, "aligned_pearson_ic": 0.03, "aligned_rank_ic": 0.03, "rank_ic_std": 0.10, "icir": 0.30, "positive_rank_ic_ratio": 0.60, "average_ic_assets": 100, "q1_return": 0.01, "q5_return": 0.02, "q5_minus_q1": 0.01, "quantile_monotonicity": 0.30, "quantile_valid_days": 180, "mean_coverage": 0.90, "coverage_pass_ratio": 0.90, "source_ticker_coverage": 0.90, "average_turnover": 0.10, "average_top_n_retention": 0.90, "average_rank_persistence": 0.80,
    }
    row.update(changes)
    return pd.DataFrame([row])


def _stability(factor_id: str = "Q1_OPERATING_MARGIN", flag: str = "STABLE") -> pd.DataFrame:
    return pd.DataFrame([{"factor_id": factor_id, "horizon": 20, "stability_flag": flag, "eligible_fold_count": 3, "negative_fold_ratio": 0.0}])


def test_frozen_candidates_keep_reporting_basis_isolated():
    by_id = {item.candidate_id: item for item in FROZEN_CANDIDATES}
    assert by_id["G1_REVENUE_YOY"].source_metric == "revenue_yoy"
    assert by_id["G4_REVENUE_CUMULATIVE_YOY"].source_metric == "revenue_cumulative_yoy"
    assert by_id["G1_REVENUE_YOY"].candidate_id != by_id["G4_REVENUE_CUMULATIVE_YOY"].candidate_id
    assert by_id["G3_EPS_YOY"].reporting_basis == "CUMULATIVE_AS_REPORTED"


def test_unsupported_or_partial_candidate_request_fails_before_statistics():
    with pytest.raises(ValidationInputError):
        validate_candidate_request(["ROE"])
    with pytest.raises(ValidationInputError):
        validate_candidate_request([item.candidate_id for item in FROZEN_CANDIDATES[:-1]])


def test_candidate_spec_is_immutable(tmp_path):
    path = tmp_path / "factor_candidate_spec.json"
    write_frozen_candidate_spec(path)
    path.write_text(path.read_text(encoding="utf-8").replace('"primary_horizon": 20', '"primary_horizon": 60'), encoding="utf-8")
    with pytest.raises(ValidationInputError):
        write_frozen_candidate_spec(path)


def test_forward_return_alignment_uses_next_open_then_horizon_open():
    start = date(2025, 1, 2)
    rows = [{"trade_date": start + timedelta(days=i), "stock_id": "2330", "adjusted_open": float(i + 1), "is_tradable_t1": True} for i in range(23)]
    result = compute_forward_returns(pd.DataFrame(rows), horizons=(20,))
    assert result.loc[0, "forward_return_20d"] == pytest.approx(22 / 2 - 1)
    assert pd.isna(result.loc[1, "forward_return_20d"]) is False
    assert pd.isna(result.loc[2, "forward_return_20d"])


def test_normalization_does_not_fill_future_or_null_values_and_is_reproducible():
    matrix = pd.DataFrame({"asof_date": ["2023-01-03"] * 4, "ticker": ["A", "B", "C", "D"], "operating_margin": [1.0, 2.0, np.nan, 4.0]})
    first = normalize_factor(matrix, _spec())
    second = normalize_factor(matrix, _spec())
    pd.testing.assert_frame_equal(first, second)
    assert pd.isna(first.loc[first.ticker.eq("C"), "rank_value"]).all()
    assert first.loc[first.ticker.eq("A"), "rank_value"].iloc[0] == pytest.approx(0.1666666667)


def test_quantile_assignment_is_deterministic_for_ties():
    matrix = pd.DataFrame({"asof_date": ["2023-01-03"] * 6, "ticker": list("ABCDEF"), "operating_margin": [1, 1, 2, 3, 4, 5]})
    result = normalize_factor(matrix, _spec())
    assert result.set_index("ticker")["quantile"].to_dict() == normalize_factor(matrix, _spec()).set_index("ticker")["quantile"].to_dict()
    assert result.loc[result.ticker.isin(["A", "B"]), "quantile"].nunique() == 1
    assert result.loc[result.ticker.eq("F"), "quantile"].iloc[0] == 5


def test_ic_is_deterministic_and_constant_inputs_are_null():
    left = pd.Series([1.0, 2.0, 3.0])
    right = pd.Series([3.0, 2.0, 1.0])
    assert _spearman(left, right) == pytest.approx(-1.0)
    assert np.isnan(_spearman(pd.Series([1.0, 1.0]), right.iloc[:2]))


def test_primary_horizon_controls_verdict_even_when_secondary_is_stronger():
    summary = pd.concat([_summary(), _summary(horizon=60, aligned_rank_ic=0.90, icir=9.0)], ignore_index=True)
    stability = pd.concat([_stability(), _stability(flag="STABLE").assign(horizon=60)], ignore_index=True)
    gate = _gate(summary, stability, {"Q1_OPERATING_MARGIN": 0.90}, pd.DataFrame([{"factor_id": "Q1_OPERATING_MARGIN", "crosscheck_status": "NOT_MEASURABLE"}]))
    assert gate.loc[0, "primary_horizon"] == PRIMARY_HORIZON
    assert gate.loc[0, "verdict"] == "ACCEPT"


def test_coverage_gate_returns_insufficient_data():
    summary = _summary(mean_coverage=0.10, coverage_pass_ratio=0.10)
    gate = _gate(summary, _stability(flag="STABLE"), {"Q1_OPERATING_MARGIN": 0.90}, pd.DataFrame([{"factor_id": "Q1_OPERATING_MARGIN", "crosscheck_status": "PASS"}]))
    assert gate.loc[0, "verdict"] == "INSUFFICIENT_DATA"
    assert not bool(gate.loc[0, "coverage_pass"])


def test_temporal_folds_are_fixed_and_chronological():
    rows = []
    for fold in FROZEN_FOLDS:
        day = pd.Timestamp(fold.start)
        rows.append({"factor_id": "Q1_OPERATING_MARGIN", "horizon": 20, "asof_date": day, "aligned_rank_ic": 0.03})
    rank = pd.DataFrame(rows)
    ic = pd.DataFrame([{**row, "aligned_pearson_ic": 0.02} for row in rows])
    quantiles = pd.DataFrame([{"factor_id": "Q1_OPERATING_MARGIN", "horizon": 20, "asof_date": row["asof_date"], "q5_minus_q1": 0.01} for row in rows])
    coverage = pd.DataFrame([{"factor_id": "Q1_OPERATING_MARGIN", "asof_date": row["asof_date"], "coverage_ratio": 0.9} for row in rows])
    result = temporal_stability(rank, quantiles, coverage, ic)
    result = result.loc[result["horizon"].eq(20)]
    assert result["fold_id"].tolist() == [fold.fold_id for fold in FROZEN_FOLDS]
    assert result["fold_start"].tolist() == [fold.start for fold in FROZEN_FOLDS]
    assert result["ic"].tolist() == [0.02] * len(FROZEN_FOLDS)


def test_rejected_factor_cannot_enter_composite():
    dates = pd.date_range("2023-01-03", periods=2)
    panel = pd.DataFrame([[0.2, 0.8], [0.3, 0.7]], index=dates, columns=["A", "B"])
    labels = pd.DataFrame({"asof_date": dates.repeat(2), "ticker": ["A", "B"] * 2, "forward_return_20d": [0.1, 0.2, 0.1, 0.2], "forward_return_60d": [0.1, 0.2, 0.1, 0.2]})
    gate = pd.DataFrame([{"factor_id": "G1_REVENUE_YOY", "verdict": "REJECT"}])
    result = incremental_composites({"G1_REVENUE_YOY": panel}, labels, ("A", "B"), gate, pd.DataFrame())
    assert result.empty


def test_composite_contains_only_accepted_factors():
    dates = pd.date_range("2023-01-03", periods=2)
    panel = pd.DataFrame([[0.2, 0.8], [0.3, 0.7]], index=dates, columns=["A", "B"])
    labels = pd.DataFrame({"asof_date": dates.repeat(2), "ticker": ["A", "B"] * 2, "forward_return_20d": [0.1, 0.2, 0.1, 0.2], "forward_return_60d": [0.1, 0.2, 0.1, 0.2]})
    gate = pd.DataFrame([{"factor_id": "G1_REVENUE_YOY", "verdict": "ACCEPT"}, {"factor_id": "G2_OPERATING_INCOME_YOY", "verdict": "REJECT"}])
    result = incremental_composites({"G1_REVENUE_YOY": panel, "G2_OPERATING_INCOME_YOY": panel}, labels, ("A", "B"), gate, pd.DataFrame())
    assert set(result["added_factor"]) == {"G1_REVENUE_YOY"}
