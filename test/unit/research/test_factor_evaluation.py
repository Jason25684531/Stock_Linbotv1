import numpy as np
import pandas as pd
import pytest

from core.research.factor_evaluation import (
    EvaluationPolicy,
    InvalidD3Input,
    annual_stability,
    build_scoreboard,
    compute_annual_results,
    compute_daily_rank_ic,
    compute_factor_correlation,
    compute_rank_autocorrelation,
    factor_status,
    load_d3_dataset,
    select_best_horizons,
    summarize_ic,
)


def test_daily_rank_ic_uses_member_rank_and_label_and_aligns_negative_direction():
    data = pd.DataFrame(
        {
            "factor_id": ["value"] * 4,
            "asof_date": ["2026-01-02"] * 4,
            "member": [True, True, True, False],
            "rank_value": [0.2, 0.5, 0.8, 0.9],
            "direction_adjusted_rank": [0.8, 0.5, 0.2, 0.1],
            "direction": [-1] * 4,
            "forward_return_1d": [0.01, 0.02, 0.03, 1.0],
        }
    )

    result = compute_daily_rank_ic(data, horizons=(1,), policy=EvaluationPolicy(min_ic_assets=3))

    row = result.iloc[0]
    assert row.effective_asset_count == 3
    assert row.raw_ic == pytest.approx(1.0)
    assert row.aligned_ic == pytest.approx(-1.0)


def test_daily_rank_ic_returns_nan_not_zero_for_insufficient_or_constant_samples():
    data = pd.DataFrame(
        {
            "factor_id": ["value"] * 3,
            "asof_date": ["2026-01-02"] * 3,
            "member": [True, True, True],
            "rank_value": [0.5, 0.5, 0.5],
            "direction_adjusted_rank": [0.5, 0.5, 0.5],
            "direction": [1, 1, 1],
            "forward_return_1d": [0.01, 0.02, None],
        }
    )

    result = compute_daily_rank_ic(data, horizons=(1,), policy=EvaluationPolicy(min_ic_assets=3))

    assert result.iloc[0].effective_asset_count == 2
    assert pd.isna(result.iloc[0].raw_ic)
    assert pd.isna(result.iloc[0].aligned_ic)


def test_scoreboard_handles_unavailable_cross_factor_correlation_without_crashing():
    status = pd.DataFrame([{"factor_id": "value", "direction": 1, "status": "WEAK", "best_horizon": "NONE", "best_horizon_confidence": "NONE"}])
    horizons = pd.DataFrame(columns=["factor_id", "horizon", "direction"])
    correlation = pd.DataFrame([{"factor_id": "value", "other_factor_id": "other", "correlation": float("nan")}])

    result = build_scoreboard(status, horizons, correlation)

    assert result.iloc[0].redundancy_flag == "UNKNOWN"
    assert pd.isna(result.iloc[0].max_abs_factor_correlation)


def test_perfect_positive_factor_has_raw_and_aligned_ic_of_one():
    data = pd.DataFrame(
        {
            "factor_id": ["momentum_20d"] * 8,
            "asof_date": ["2026-01-02"] * 4 + ["2026-01-05"] * 4,
            "member": [True] * 8,
            "rank_value": [0.1, 0.3, 0.6, 0.9] * 2,
            "direction_adjusted_rank": [0.1, 0.3, 0.6, 0.9] * 2,
            "direction": [1] * 8,
            "forward_return_1d": [0.01, 0.02, 0.03, 0.04] * 2,
        }
    )

    daily = compute_daily_rank_ic(data, horizons=(1,), policy=EvaluationPolicy(min_ic_assets=4))
    summary = summarize_ic(daily).iloc[0]

    assert daily["raw_ic"].tolist() == pytest.approx([1.0, 1.0])
    assert daily["aligned_ic"].tolist() == pytest.approx([1.0, 1.0])
    assert summary.raw_mean_ic == pytest.approx(1.0)
    assert summary.aligned_mean_ic == pytest.approx(1.0)


def test_perfect_negative_direction_has_raw_minus_one_and_aligned_plus_one():
    data = pd.DataFrame(
        {
            "factor_id": ["momentum_20d"] * 4,
            "asof_date": ["2026-01-02"] * 4,
            "member": [True] * 4,
            "rank_value": [0.1, 0.3, 0.6, 0.9],
            "direction_adjusted_rank": [0.9, 0.6, 0.3, 0.1],
            "direction": [-1] * 4,
            "forward_return_1d": [0.04, 0.03, 0.02, 0.01],
        }
    )
    row = compute_daily_rank_ic(data, horizons=(1,), policy=EvaluationPolicy(min_ic_assets=4)).iloc[0]
    assert row.raw_ic == pytest.approx(-1.0)
    assert row.aligned_ic == pytest.approx(1.0)


def test_direction_zero_has_no_aligned_ic_and_seeded_random_ic_is_near_zero():
    rng = np.random.default_rng(20260811)
    rows = []
    for day in pd.date_range("2026-01-02", periods=20, freq="B"):
        for asset, (rank, label) in enumerate(zip(rng.random(80), rng.random(80))):
            rows.append({"factor_id": "vwap_gap", "asof_date": day, "member": True, "rank_value": rank, "direction_adjusted_rank": np.nan, "direction": 0, "forward_return_1d": label, "asset_id": str(asset)})
    daily = compute_daily_rank_ic(pd.DataFrame(rows), horizons=(1,), policy=EvaluationPolicy(min_ic_assets=30))

    assert daily["aligned_ic"].isna().all()
    assert abs(daily["raw_ic"].mean()) < 0.10


def test_overlap_autocorrelation_and_daily_factor_correlation_use_only_shared_assets():
    data = pd.DataFrame(
        {
            "asof_date": ["2026-01-02"] * 6 + ["2026-01-05"] * 6,
            "asset_id": ["A", "B", "C"] * 4,
            "factor_id": ["momentum_20d"] * 3 + ["momentum_60d"] * 3 + ["momentum_20d"] * 3 + ["momentum_60d"] * 3,
            "member": [True] * 12,
            "direction": [1] * 12,
            "rank_value": [0.1, 0.5, 0.9, 0.1, 0.5, 0.9, 0.2, 0.6, 0.8, 0.8, 0.4, 0.2],
            "direction_adjusted_rank": [0.1, 0.5, 0.9, 0.1, 0.5, 0.9, 0.2, 0.6, 0.8, 0.8, 0.4, 0.2],
        }
    )

    autocorrelation = compute_rank_autocorrelation(data)
    correlation = compute_factor_correlation(data)

    assert autocorrelation.loc[autocorrelation.factor_id.eq("momentum_20d"), "overlapping_asset_count"].iloc[0] == 3
    assert autocorrelation.loc[autocorrelation.factor_id.eq("momentum_20d"), "rank_autocorrelation"].iloc[0] == pytest.approx(1.0)
    assert correlation.loc[(correlation.factor_id.eq("momentum_20d")) & (correlation.other_factor_id.eq("momentum_60d")), "correlation"].iloc[0] == pytest.approx(0.0)


def test_correlation_recovers_identical_and_opposite_factors_and_annual_years():
    factor_rows = []
    for factor_id, ranks in (("momentum_20d", [0.1, 0.5, 0.9]), ("momentum_60d", [0.1, 0.5, 0.9]), ("momentum_12_1", [0.9, 0.5, 0.1])):
        for asset, rank in enumerate(ranks):
            factor_rows.append({"asof_date": "2026-01-02", "asset_id": str(asset), "factor_id": factor_id, "member": True, "rank_value": rank})
    correlation = compute_factor_correlation(pd.DataFrame(factor_rows))
    assert correlation.loc[(correlation.factor_id.eq("momentum_20d")) & (correlation.other_factor_id.eq("momentum_60d")), "correlation"].iloc[0] == pytest.approx(1.0)
    assert correlation.loc[(correlation.factor_id.eq("momentum_20d")) & (correlation.other_factor_id.eq("momentum_12_1")), "correlation"].iloc[0] == pytest.approx(-1.0)

    daily = pd.DataFrame(
        {"factor_id": ["momentum_20d"] * 2, "horizon": [1, 1], "asof_date": ["2025-12-31", "2026-01-02"], "direction": [1, 1], "raw_ic": [0.1, 0.2], "aligned_ic": [0.1, 0.2], "effective_asset_count": [30, 30]}
    )
    assert compute_annual_results(daily)["year"].tolist() == [2025, 2026]


def test_stability_and_status_preserve_signed_aligned_rules_and_review_cap():
    annual = pd.DataFrame(
        [
            {"factor_id": "momentum_20d", "horizon": 1, "direction": 1, "annual_valid_ic_days": 60, "aligned_mean_ic": 0.03},
            {"factor_id": "momentum_20d", "horizon": 1, "direction": 1, "annual_valid_ic_days": 60, "aligned_mean_ic": -0.03},
            {"factor_id": "momentum_20d", "horizon": 1, "direction": 1, "annual_valid_ic_days": 60, "aligned_mean_ic": -0.03},
        ]
    )
    stability = annual_stability(annual, policy=EvaluationPolicy(max_negative_year_ratio=0.50))
    assert stability.iloc[0].stability_flag == "UNSTABLE"

    horizons = pd.DataFrame(
        [
            {"factor_id": "negative", "horizon": 1, "direction": -1, "horizon_eligible": False, "raw_valid_ic_days": 130, "core_pass": False, "aligned_icir": -3.0, "aligned_mean_ic": -0.05},
            {"factor_id": "negative", "horizon": 5, "direction": -1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 0.3, "aligned_mean_ic": 0.03, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.1},
            {"factor_id": "unknown", "horizon": 1, "direction": 0, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "raw_icir": -0.4, "raw_mean_ic": -0.04, "stability_flag": "NOT_APPLICABLE", "average_equal_weight_turnover": 0.1},
        ]
    )
    best = select_best_horizons(horizons)
    statuses = factor_status(horizons, best, policy=EvaluationPolicy())

    assert best.loc[best.factor_id.eq("negative"), "best_horizon"].iloc[0] == 5
    assert statuses.loc[statuses.factor_id.eq("negative"), "status"].iloc[0] == "CANDIDATE"
    assert statuses.loc[statuses.factor_id.eq("unknown"), "status"].iloc[0] == "REVIEW"


def test_best_horizon_confidence_and_status_ladder_cover_public_outcomes():
    rows = [
        {"factor_id": "none", "horizon": 1, "direction": 1, "horizon_eligible": False, "raw_valid_ic_days": 20, "core_pass": False, "aligned_icir": -1.0, "aligned_mean_ic": -0.1},
        {"factor_id": "one", "horizon": 1, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 0.4, "aligned_mean_ic": 0.04, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.1},
        {"factor_id": "clear", "horizon": 1, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 1.0, "aligned_mean_ic": 0.10, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.1},
        {"factor_id": "clear", "horizon": 5, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 0.2, "aligned_mean_ic": 0.03, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.1},
        {"factor_id": "conflict", "horizon": 1, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 1.0, "aligned_mean_ic": 0.03, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.1},
        {"factor_id": "conflict", "horizon": 5, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 0.9, "aligned_mean_ic": 0.04, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.1},
        {"factor_id": "weak", "horizon": 1, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": False, "aligned_icir": 0.4, "aligned_mean_ic": 0.04},
        {"factor_id": "turnover", "horizon": 1, "direction": 1, "horizon_eligible": True, "raw_valid_ic_days": 130, "core_pass": True, "aligned_icir": 0.4, "aligned_mean_ic": 0.04, "stability_flag": "STABLE", "average_equal_weight_turnover": 0.51},
    ]
    horizons = pd.DataFrame(rows)
    best = select_best_horizons(horizons)
    statuses = factor_status(horizons, best, policy=EvaluationPolicy())

    assert best.set_index("factor_id").loc["none"].best_horizon == "NONE"
    assert best.set_index("factor_id").loc["one"].best_horizon_confidence == "CLEAR"
    assert best.set_index("factor_id").loc["clear"].best_horizon_confidence == "CLEAR"
    assert best.set_index("factor_id").loc["conflict"].best_horizon_confidence == "CONFLICT"
    assert statuses.set_index("factor_id").loc["none"].status == "UNTESTED"
    assert statuses.set_index("factor_id").loc["weak"].status == "WEAK"
    assert statuses.set_index("factor_id").loc["turnover"].status == "REVIEW"


def test_loader_rejects_duplicate_keys_and_leakage_before_any_output(tmp_path):
    run = tmp_path / "d3"
    partition = run / "research_dataset" / "momentum_20d"
    partition.mkdir(parents=True)
    (run / "run_manifest.json").write_text('{"status":"success","d3_enabled":true,"leakage_failure_count":0}', encoding="utf-8")
    row = {"asof_date": "2026-01-02", "asset_id": "A", "factor_id": "momentum_20d", "raw_value": 1.0, "winsorized_value": 1.0, "rank_value": 0.5, "direction": 1, "direction_adjusted_rank": 0.5, "member": True, "forward_return_1d": 0.01, "forward_return_5d": 0.01, "forward_return_10d": 0.01, "forward_return_20d": 0.01, "forward_return_60d": 0.01}
    pd.DataFrame([row, row]).to_csv(partition / "2026.csv", index=False)

    with pytest.raises(InvalidD3Input, match="duplicate"):
        load_d3_dataset(run, factors=("momentum_20d",))


def test_loader_rejects_missing_requested_factor_and_leakage_manifest(tmp_path):
    run = tmp_path / "d3"
    partition = run / "research_dataset" / "momentum_20d"
    partition.mkdir(parents=True)
    row = {"asof_date": "2026-01-02", "asset_id": "A", "factor_id": "momentum_20d", "raw_value": 1.0, "winsorized_value": 1.0, "rank_value": 0.5, "direction": 1, "direction_adjusted_rank": 0.5, "member": True, "forward_return_1d": 0.01, "forward_return_5d": 0.01, "forward_return_10d": 0.01, "forward_return_20d": 0.01, "forward_return_60d": 0.01}
    pd.DataFrame([row]).to_csv(partition / "2026.csv", index=False)
    (run / "run_manifest.json").write_text('{"status":"success","d3_enabled":true,"leakage_failure_count":0}', encoding="utf-8")

    with pytest.raises(InvalidD3Input, match="missing requested factors"):
        load_d3_dataset(run, factors=("momentum_20d", "momentum_60d"))

    (run / "run_manifest.json").write_text('{"status":"success","d3_enabled":true,"leakage_failure_count":1}', encoding="utf-8")
    with pytest.raises(InvalidD3Input, match="not an accepted"):
        load_d3_dataset(run, factors=("momentum_20d",))


def test_loader_rejects_missing_or_malformed_manifest_and_infinite_values(tmp_path):
    with pytest.raises(InvalidD3Input, match="missing"):
        load_d3_dataset(tmp_path / "absent", factors=("momentum_20d",))

    run = tmp_path / "d3"
    (run / "research_dataset").mkdir(parents=True)
    (run / "run_manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(InvalidD3Input, match="malformed"):
        load_d3_dataset(run, factors=("momentum_20d",))

    partition = run / "research_dataset" / "momentum_20d"
    partition.mkdir()
    (run / "run_manifest.json").write_text('{"status":"success","d3_enabled":true,"leakage_failure_count":0}', encoding="utf-8")
    row = {"asof_date": "2026-01-02", "asset_id": "A", "factor_id": "momentum_20d", "raw_value": float("inf"), "winsorized_value": 1.0, "rank_value": 0.5, "direction": 1, "direction_adjusted_rank": 0.5, "member": True, "forward_return_1d": 0.01, "forward_return_5d": 0.01, "forward_return_10d": 0.01, "forward_return_20d": 0.01, "forward_return_60d": 0.01}
    pd.DataFrame([row]).to_csv(partition / "2026.csv", index=False)
    with pytest.raises(InvalidD3Input, match="infinite"):
        load_d3_dataset(run, factors=("momentum_20d",))
