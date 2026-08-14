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


def test_unknown_redundancy_share_never_increases_during_normalization():
    from core.research.composite_factor import build_factor_weights

    candidates = pd.DataFrame({"factor_id": ["known", "unknown"], "mean_ic": [2.0, 1.0], "icir": [2.0, 1.0]})
    correlation = pd.DataFrame([[1.0, 0.9], [0.9, np.nan]], index=candidates.factor_id, columns=candidates.factor_id)

    weights = build_factor_weights(candidates, correlation, "redundancy_adjusted").set_index("factor_id")

    assert weights.loc["unknown", "final_factor_weight"] == pytest.approx(weights.loc["unknown", "base_quality_weight"])
    assert weights.final_factor_weight.sum() == pytest.approx(1.0)


def test_target_weights_freeze_t_plus_one_execution_date():
    from core.research.target_weights import build_target_weights

    scores = pd.DataFrame({"asof_date": ["2026-01-02", "2026-01-02"], "asset_id": ["A", "B"], "composite_score": [0.9, 0.8]})
    universe = pd.DataFrame({"asof_date": ["2026-01-02", "2026-01-02"], "asset_id": ["A", "B"], "member": [True, True], "is_tradable_t1": [True, True], "execution_date": ["2026-01-05", "2026-01-05"]})

    weights = build_target_weights(scores, universe, config_id="c", top_n=2, stock_weighting="equal")

    assert weights.execution_date.tolist() == [pd.Timestamp("2026-01-05")] * 2
    assert weights.target_weight.sum() == pytest.approx(1.0)


def test_shortlist_zscore_has_defined_zero_std_nan_and_inf_behavior():
    from core.research.portfolio_research import safe_zscore

    result = safe_zscore(pd.Series([2.0, 2.0, np.nan, np.inf]))

    assert result.tolist() == [0.0, 0.0, 0.0, 0.0]


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
