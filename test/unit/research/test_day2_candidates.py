import numpy as np
import pandas as pd

from core.research.day2_candidates import (
    D4_POLICY,
    STRICT_OOS_END,
    STRICT_OOS_START,
    TRAIN_END,
    TRAIN_START,
    UNIV_RESEARCH_V1,
    UNIV_RUNTIME_V1,
    VALIDATION_END,
    VALIDATION_START,
    candidate_specs,
    compute_candidate,
    frozen_parameters,
)


MASTER = "outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv"


def test_day2_candidate_manifest_is_frozen_and_unique():
    specs = candidate_specs(MASTER)
    assert len(specs) == 58
    assert len({item.candidate_id for item in specs}) == 58
    assert sum(item.tier == "TIER3" for item in specs) == 37
    assert all(item.universe_id in {UNIV_RESEARCH_V1, UNIV_RUNTIME_V1} for item in specs)
    assert all(item.vwap_definition == "SOURCE_APPROX_VWAP" for item in specs if item.candidate_id in {"A101_005", "A101_011", "A101_041"})


def test_frozen_parameters_match_design():
    params = frozen_parameters()
    assert params["evaluation_policy"]["min_effective_days"] == D4_POLICY.min_effective_days == 120
    assert params["date_split"] == {
        "train_start": str(TRAIN_START.date()),
        "train_end": str(TRAIN_END.date()),
        "validation_start": str(VALIDATION_START.date()),
        "validation_end": str(VALIDATION_END.date()),
        "strict_oos_start": str(STRICT_OOS_START.date()),
        "strict_oos_end": str(STRICT_OOS_END.date()),
    }


def test_alpha191_088_adapter_uses_canonical_panel_formula():
    dates = pd.date_range("2023-01-01", periods=25)
    columns = ["1101", "2330"]
    close = pd.DataFrame(np.arange(50, dtype=float).reshape(25, 2) + 100, index=dates, columns=columns)
    frames = {
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": pd.DataFrame(100.0, index=dates, columns=columns),
        "amount": close * 100,
    }
    specs = {item.candidate_id: item for item in candidate_specs(MASTER)}
    result = compute_candidate(specs["A191_088"], frames)
    assert np.isnan(result.iloc[0, 0])
    assert result.iloc[-1, 0] == (close.iloc[-1, 0] - close.iloc[4, 0]) / close.iloc[4, 0] * 100
    assert result.iloc[-1, 0] > 0


def test_volume_direction_variants_keep_raw_values_for_signed_evaluation():
    dates = pd.date_range("2023-01-01", periods=21)
    columns = ["1101", "2330"]
    volume = pd.DataFrame(100.0, index=dates, columns=columns)
    close = pd.DataFrame(100.0, index=dates, columns=columns)
    frames = {"open": close, "high": close, "low": close, "close": close, "volume": volume, "amount": volume * close}
    specs = {item.candidate_id: item for item in candidate_specs(MASTER)}
    positive = compute_candidate(specs["RS_008_POSITIVE"], frames)
    negative = compute_candidate(specs["RS_008_NEGATIVE"], frames)
    pd.testing.assert_frame_equal(positive, negative)
