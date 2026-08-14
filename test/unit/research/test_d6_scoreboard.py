import pytest

from core.research.portfolio_validation import build_validation_scoreboard, classify_validation_status


def test_engine_fail_is_invalid():
    assert classify_validation_status(engine_status="FAIL", temporal_segment_sharpes=[1, 1, 1], cost_stress_total_returns=[0.1, 0.1, 0.1], hac_alpha=0.001, hac_p_value=0.01) == "INVALID"


def test_negative_temporal_segment_is_weak():
    assert classify_validation_status(engine_status="PASS", temporal_segment_sharpes=[1.0, -0.2, 1.0], cost_stress_total_returns=[0.1, 0.1, 0.1], hac_alpha=0.001, hac_p_value=0.01) == "WEAK"


def test_failed_cost_stress_is_weak():
    assert classify_validation_status(engine_status="PASS", temporal_segment_sharpes=[1, 1, 1], cost_stress_total_returns=[0.1, 0.1, -0.05], hac_alpha=0.001, hac_p_value=0.01) == "WEAK"


def test_significant_p_value_alone_is_not_sufficient_for_robust():
    # p<0.05 but a temporal segment fails -> must not be ROBUST just because of significance
    status = classify_validation_status(engine_status="PASS", temporal_segment_sharpes=[1.0, -0.1, 1.0], cost_stress_total_returns=[0.1, 0.1, 0.1], hac_alpha=0.002, hac_p_value=0.001)
    assert status != "ROBUST"
    assert status == "WEAK"


def test_insignificant_p_value_is_review_not_robust():
    status = classify_validation_status(engine_status="PASS", temporal_segment_sharpes=[1, 1, 1], cost_stress_total_returns=[0.1, 0.1, 0.1], hac_alpha=0.001, hac_p_value=0.5)
    assert status == "REVIEW"


def test_all_gates_pass_is_robust():
    status = classify_validation_status(engine_status="PASS_WITH_EXPLAINED_DIFFERENCES", temporal_segment_sharpes=[1, 1, 1], cost_stress_total_returns=[0.1, 0.1, 0.05], hac_alpha=0.001, hac_p_value=0.01)
    assert status == "ROBUST"


def test_scoreboard_requires_every_frozen_config():
    rows = [{"config_id": "a"}, {"config_id": "b"}]
    with pytest.raises(ValueError, match="missing frozen shortlist configs"):
        build_validation_scoreboard(rows, ["a", "b", "c"])


def test_scoreboard_accepts_complete_set():
    rows = [{"config_id": "a"}, {"config_id": "b"}]
    frame = build_validation_scoreboard(rows, ["a", "b"])
    assert len(frame) == 2
