"""Regression tests for the Day 2 review remediation (see day2_review_report.md).

They pin the behaviours the first review found missing: a real alphalens
crosscheck, true OOS same-sign, a data-driven conflict tally, and an honest
Tier 2 status.
"""

import importlib.util
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "rfv", Path(__file__).resolve().parents[3] / "jobs" / "run_factor_validation.py"
)
rfv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rfv)


def _alpha_row(candidate_id, mean_ic, q5_minus_q1, turnover=0.2):
    return {"candidate_id": candidate_id, "universe_id": "UNIV_RESEARCH_V1", "role": "Alpha",
            "mean_ic": mean_ic, "q5_minus_q1": q5_minus_q1, "turnover": turnover}


def _synthetic_factor(sign=1.0):
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=120, freq="B")
    assets = [f"{1000 + i}" for i in range(60)]
    rows = []
    for date in dates:
        factor = rng.normal(size=60)
        forward = {h: coef * factor + rng.normal(size=60) * 0.5 for h, coef in ((5, 0.3), (20, 0.5), (60, 0.7))}
        for i, asset in enumerate(assets):
            rows.append({"asof_date": date, "asset_id": asset, "raw_value": sign * factor[i], "member": True,
                         "forward_return_5d": forward[5][i], "forward_return_20d": forward[20][i], "forward_return_60d": forward[60][i]})
    return pd.DataFrame(rows)


# TEST 1 + 2: OOS sign reversal must be caught, agreement must pass.
def test_oos_same_sign_detects_reversal():
    assert rfv._same_sign(0.03, 0.05) is True
    assert rfv._same_sign(0.03, -0.05) is False   # reversal, was the old bug
    assert rfv._same_sign(-0.02, -0.01) is True
    assert rfv._same_sign(-0.02, 0.01) is False
    assert rfv._same_sign(0.0, 0.05) is False      # non-zero requirement
    assert rfv._same_sign(np.nan, 0.05) is False


# TEST 3: alphalens is actually executed (not a version lookup) and tracks the factor.
def test_alphalens_is_really_executed():
    positive = rfv._alphalens_metrics(_synthetic_factor(+1.0), 20)
    negative = rfv._alphalens_metrics(_synthetic_factor(-1.0), 20)
    assert positive["status"] == "OK"
    assert positive["mean_ic"] > 0 and positive["spread"] > 0
    assert negative["mean_ic"] < 0            # sign flips with the factor -> real recompute
    assert positive["mean_ic"] == -negative["mean_ic"]


# TEST 4: internal vs alphalens conclusion mismatch -> CROSSCHECK_FAIL; agreement -> PASS.
def test_crosscheck_flags_alphalens_mismatch():
    scoreboard = pd.DataFrame([_alpha_row("AGREE", 0.03, 0.02), _alpha_row("DISAGREE", 0.03, 0.02)])
    alphalens_by_id = {
        "AGREE": {"status": "OK", "mean_ic": 0.028, "spread": 0.019, "autocorr": 0.1},
        "DISAGREE": {"status": "OK", "mean_ic": -0.05, "spread": -0.02, "autocorr": 0.1},
    }
    out = rfv._crosscheck(scoreboard, alphalens_by_id).set_index("factor_id")
    assert out.loc["AGREE", "crosscheck"] == "PASS"
    assert out.loc["DISAGREE", "crosscheck"] == "CROSSCHECK_FAIL"
    # not measurable must not masquerade as PASS
    out2 = rfv._crosscheck(pd.DataFrame([_alpha_row("NM", 0.03, 0.02)]), {"NM": {"status": "ALPHALENS_NOT_MEASURABLE"}})
    assert out2.iloc[0]["crosscheck"] == "ALPHALENS_NOT_MEASURABLE"


# TEST 5: a non-PASS crosscheck candidate cannot enter the accepted pool.
def test_accepted_pool_excludes_non_pass_crosscheck():
    base = {"pit_status": "VERIFIED", "oos_clean": "CLEAN", "verdict": "CANDIDATE"}
    scoreboard = pd.DataFrame([
        {"candidate_id": "OK", "crosscheck": "PASS", **base},
        {"candidate_id": "FAIL", "crosscheck": "CROSSCHECK_FAIL", **base},
        {"candidate_id": "NM", "crosscheck": "ALPHALENS_NOT_MEASURABLE", **base},
    ])
    assert list(rfv._accepted_pool(scoreboard)["candidate_id"]) == ["OK"]


# TEST 6: runtime conflict tally is derived from verdicts, not hardcoded.
def test_runtime_conflicts_tally_is_computed():
    scoreboard = pd.DataFrame([
        {"candidate_id": "RS_008", "horizon_5_mean_ic": np.nan, "horizon_20_mean_ic": np.nan, "horizon_60_mean_ic": np.nan, "risk_utility_verdict": "NOT_APPLICABLE", "mean_ic": np.nan},
        {"candidate_id": "RS_012", "horizon_5_mean_ic": np.nan, "horizon_20_mean_ic": np.nan, "horizon_60_mean_ic": np.nan, "risk_utility_verdict": "KEEP_AS_RISK_ONLY", "mean_ic": np.nan},
    ])
    correlations = pd.DataFrame([{"factor_id": "RS_012", "other_factor_id": "RS_011", "correlation": 0.9}])
    conflicts = rfv._runtime_conflicts(scoreboard, correlations)
    by_id = {c["conflict_id"]: c for c in conflicts}
    assert by_id["volume_ratio_20d_direction"]["resolution_status"] == "UNRESOLVED"   # no signal
    assert by_id["natr_14d_role"]["resolution_status"] == "RESOLVED"                  # KEEP_AS_RISK_ONLY
    assert by_id["universe_mismatch"]["resolution_status"] == "UNRESOLVED"
    tally = Counter(c["resolution_status"] for c in conflicts)
    assert tally["UNRESOLVED"] >= 2 and sum(tally.values()) == 5


# TEST 7: Tier 2 with DB unavailable is honest, never silently COMPLETE.
def test_tier2_db_unavailable_is_not_silently_complete():
    scoreboard = pd.DataFrame([
        {"candidate_id": "T2_REVENUE_YOY", "tier": "TIER2", "family": "A", "data_status": "NO_DATA", "pit_status": "PIT_RISK"},
        {"candidate_id": "T2_EPS_LEVEL", "tier": "TIER2", "family": "C", "data_status": "NO_DATA", "pit_status": "PIT_RISK"},
    ])
    pit_evidence = [{"source": "database", "status": "PIT_RISK", "detail": "unavailable: ..."}]
    status, counts, day2_status = rfv._tier2_summary(scoreboard, pit_evidence)
    assert status == "NOT_ESTABLISHED_DB_UNAVAILABLE"
    assert counts["validated"] == 0
    assert day2_status == "COMPLETE_WITH_TIER2_BLOCKER"
