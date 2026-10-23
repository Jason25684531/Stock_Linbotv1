"""Track A engineering fixes (OD-28): history-based dealer streaks, model-complete z-score population, rules-only runs,
model contract fields and the approval-gated training builder. Synthetic data only; no model is fitted."""
import json
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest

from core.calc_indicators import build_feature_history
from core.research import model_training, native_models
from core.research.native_frame import NativeResearchData
from core.research.native_models import ModelContractError, load_model_contract
from test_daily_selection_recovery import chips
from test_native_recovery import DAYS, LAST, fundamentals, snapshot

MODEL_INPUTS = ["rsi", "bias", "foreign_ratio", "trust_ratio", "dealer_ratio", "chip_score", "dealer_consec_days", "foreign_consec_days"]


def provider(snap=None):
    return NativeResearchData(chips(snapshot()) if snap is None else snap, *fundamentals())


# ---- 1. dealer_consec_days follows the stock history ----
def test_dealer_consec_days_counts_consecutive_sessions_and_stays_null_without_a_flow():
    snap = snapshot().assign(dealer_buy=0.0)
    last5 = (snap.stock_id == "1101") & (snap.trade_date >= DAYS[-5])
    snap.loc[last5, "dealer_buy"] = 10.0
    features = build_feature_history(snap)
    row = features[(features.stock_id == "1101") & (features.trade_date == LAST)].iloc[0]
    assert row.dealer_consec_days == 5  # the old one-day cross-section could only give 0 or 1
    missing = build_feature_history(snap.assign(dealer_buy=np.nan))
    assert missing[missing.trade_date == LAST].dealer_consec_days.isna().all()


def test_institutional_consec_buy_days_uses_the_history_streak():
    snap = chips(snapshot())
    snap.loc[(snap.stock_id == "1101") & (snap.trade_date >= DAYS[-5]), ["foreign_buy", "trust_buy", "dealer_buy"]] = 10.0
    frame, _ = NativeResearchData(snap, *fundamentals()).frame(LAST.strftime("%Y-%m-%d"), [], model_inputs=MODEL_INPUTS)
    row = frame.set_index("stock_id").loc["1101"]
    assert row.dealer_consec_days == 5 and row.institutional_consec_buy_days == row.foreign_consec_days + row.trust_consec_days + 5


# ---- 2. z-score population = rows with every model input; rules come after ----
def zscores(frame):
    return frame.set_index("stock_id")[["rsi_z", "chip_score_z"]]


def test_rule_inputs_never_change_the_zscore_population():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    base, _ = data.frame(day, [], model_inputs=MODEL_INPUTS)
    full = data.cross_section(LAST).copy()
    full.loc[full.stock_id == "1101", "revenue_yoy"] = np.nan  # a rule-only input is missing for one stock
    data._cache[LAST] = full
    with_rule, note = data.frame(day, ["revenue_yoy"], model_inputs=MODEL_INPUTS)
    assert list(with_rule.stock_id) == ["1102"] and note["excluded_rule_missing"][0]["stock_id"] == "1101"
    pd.testing.assert_frame_equal(zscores(with_rule), zscores(base).loc[["1102"]])  # the dropped stock still counted in the population


def test_a_row_missing_a_model_input_is_outside_the_population():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    full = data.cross_section(LAST).copy()
    full.loc[full.stock_id == "1101", "dealer_ratio"] = np.nan
    data._cache[LAST] = full
    kept, note = data.frame(day, [], model_inputs=MODEL_INPUTS)
    assert list(kept.stock_id) == ["1102"] and note["excluded_model_incomplete"][0]["missing_columns"] == ["dealer_ratio"]
    alone, _ = provider(snapshot().query("stock_id == '1102'").pipe(chips)).frame(day, [], model_inputs=MODEL_INPUTS)
    assert alone.rsi_z.iloc[0] == 0 and kept.rsi_z.iloc[0] == 0  # a population of one has no spread: z = 0, never a filled value


def test_legacy_frame_behavior_is_unchanged_without_model_inputs():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    legacy, note = data.frame(day, ["rsi"])
    assert "excluded_model_incomplete" not in note and len(legacy) == 2


# ---- 3. model contract: training_data_end and specification hash ----
def model_file(tmp_path, monkeypatch, **extra):
    features = ["rsi_z", "chip_score_z"]
    payload = {"model": SimpleNamespace(feature_importances_=[0.6, 0.4]), "features": features, "training_date": "2026-05-20 20:55:00", "version": "t", **extra}
    joblib.dump(payload, tmp_path / "stock_ai_model_vx.pkl")
    monkeypatch.setattr(native_models, "get_model_path", lambda name: str(tmp_path / f"stock_ai_model_{name}.pkl"))


def test_training_data_end_bounds_the_window_and_the_spec_hash_is_checked(tmp_path, monkeypatch):
    model_file(tmp_path, monkeypatch, training_data_end="2026-10-22", spec_sha256="a" * 64)
    available = {"rsi", "chip_score", "foreign_ratio", "trust_ratio", "dealer_ratio", "margin_change_pct"}
    with pytest.raises(ModelContractError, match="WINDOW_NOT_AFTER_TRAINING_DATA_END"):
        load_model_contract("vx", available, "2026-10-22")
    contract = load_model_contract("vx", available, "2026-10-23", expected_spec_sha256="a" * 64)
    assert contract.identity["training_data_end"] == "2026-10-22" and contract.identity["spec_sha256"] == "a" * 64
    with pytest.raises(ModelContractError, match="SPEC_HASH_MISMATCH"):
        load_model_contract("vx", available, "2026-10-23", expected_spec_sha256="b" * 64)


def test_a_model_with_a_spec_hash_requires_every_feature_not_only_those_with_importance(tmp_path, monkeypatch):
    features = ["rsi_z", "natr_z"]
    joblib.dump({"model": SimpleNamespace(feature_importances_=[1.0, 0.0]), "features": features, "training_date": "2026-05-20 20:55:00",
                 "version": "t", "training_data_end": "2026-05-20", "spec_sha256": "a" * 64}, tmp_path / "stock_ai_model_vy.pkl")
    monkeypatch.setattr(native_models, "get_model_path", lambda name: str(tmp_path / f"stock_ai_model_{name}.pkl"))
    assert load_model_contract("vy", {"rsi", "natr"}, "2026-10-23").required_raw == ["natr", "rsi"]  # same population in training and inference


def test_an_old_model_without_the_new_fields_still_loads_as_before(tmp_path, monkeypatch):
    model_file(tmp_path, monkeypatch)
    contract = load_model_contract("vx", {"rsi", "chip_score", "foreign_ratio", "trust_ratio", "dealer_ratio", "margin_change_pct"}, "2026-10-23")
    assert contract.identity["training_data_end"] is None and contract.identity["spec_sha256"] is None


def test_recovered_status_only_blocks_on_missing_cutoff_or_unproven_chip_definition():
    from jobs.run_daily import recovered_model_status

    assert recovered_model_status({"training_data_end": None, "features_used": ["chip_score_z"], "spec_sha256": None}).count("CHIP_SCORE") == 1
    assert recovered_model_status({"training_data_end": "2026-10-22", "features_used": ["chip_score_z"], "spec_sha256": "a" * 64}) is None
    assert "TRAINING_DATA_END_MISSING" in recovered_model_status({"training_data_end": None, "features_used": [], "spec_sha256": "a" * 64})


# ---- 4. training builder: approval gate and parity with the recovered frame ----
class Untouchable:
    def __getattr__(self, name):
        raise AssertionError(f"data was read before approval: {name}")


SPEC = {"sha256": "x", "feature_order": ["rsi_z", "chip_score_z"], "strategies": {}}


def test_training_refuses_without_a_matching_approval_before_reading_any_data(tmp_path):
    spec = {**SPEC, "sha256": model_training.spec_hash({k: v for k, v in SPEC.items() if k != "sha256"})}
    with pytest.raises(model_training.OwnerApprovalError, match="approval file missing"):
        model_training.train(Untouchable(), [], spec, "mean_reversion", tmp_path / "none.json", tmp_path)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"spec_sha256": "0" * 64, "approved_by": "owner"}), encoding="utf-8")
    with pytest.raises(model_training.OwnerApprovalError, match="does not match"):
        model_training.train(Untouchable(), [], spec, "mean_reversion", approval, tmp_path)
    approval.write_text(json.dumps({"spec_sha256": spec["sha256"]}), encoding="utf-8")
    with pytest.raises(model_training.OwnerApprovalError, match="approved_by"):
        model_training.train(Untouchable(), [], spec, "mean_reversion", approval, tmp_path)


def test_training_frame_equals_the_recovered_frame_for_the_same_session():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    spec = {"feature_order": ["rsi_z", "bias_z", "chip_score_z"], "model_inputs": MODEL_INPUTS,
            "strategies": {"mean_reversion": {"label": {"look_ahead_sessions": 3, "target_return": 0.01}}}}
    training = model_training.build_training_frame(data, [DAYS[-6]], spec, "mean_reversion", data_end=day)
    recovered, _ = data.frame(DAYS[-6].strftime("%Y-%m-%d"), [], model_inputs=MODEL_INPUTS)
    columns = spec["feature_order"]
    pd.testing.assert_frame_equal(training[["stock_id", *columns]].reset_index(drop=True), recovered[["stock_id", *columns]].reset_index(drop=True),
                                  rtol=1e-9, atol=1e-12)
    assert list(training.columns) == ["stock_id", "trade_date", *columns, "label"]


def test_labels_use_only_the_next_sessions_and_rows_without_a_full_horizon_are_dropped():
    snap = snapshot().assign(dealer_buy=1.0, margin_balance=1000.0)
    day = DAYS[-6]
    high = snap.loc[(snap.stock_id == "1101") & (snap.trade_date == DAYS[-5]), "high_price"].iloc[0]
    close = snap.loc[(snap.stock_id == "1101") & (snap.trade_date == day), "close_price"].iloc[0]
    spec = {"feature_order": ["rsi_z"], "model_inputs": ["rsi"], "strategies": {"m": {"label": {"look_ahead_sessions": 3, "target_return": 0.0}}}}
    data = NativeResearchData(snap, *fundamentals())
    frame = model_training.build_training_frame(data, [day, DAYS[-2]], spec, "m", data_end=LAST.strftime("%Y-%m-%d"))
    assert set(frame.trade_date) == {day}  # DAYS[-2] has only one session ahead: dropped, not labelled 0
    expected = int(snap[(snap.stock_id == "1101") & snap.trade_date.isin(DAYS[-5:-2])].high_price.max() / close - 1 > 0.0)
    assert int(frame[frame.stock_id == "1101"].label.iloc[0]) == expected and high > 0


# ---- 5. rules-only research runs ----
def test_rules_only_engine_needs_no_model_and_excludes_only_on_rule_inputs():
    from core.backtest.runner import BacktestEngine
    from scripts.native_recovery import isolated

    snap = snapshot().assign(dealer_buy=np.nan, margin_balance=np.nan)  # the AI features are unavailable at source
    data = NativeResearchData(snap, *fundamentals())
    sessions = [d.strftime("%Y-%m-%d") for d in DAYS[60:]]
    with isolated(data, {}):
        engine = BacktestEngine(mode="mean_reversion", start_date=sessions[0], end_date=sessions[-1], persist_results=False, persist_to_db=False,
                                execution_contract="next_session_open_v2", trading_sessions=sessions, research_data=data, rules_only=True)
        assert engine.model is None and engine.model_identity is None
        from core.research.native_frame import rule_inputs
        assert engine.required_inputs == sorted(rule_inputs(engine.strategy_obj))
        assert isinstance(engine.find_candidates(sessions[-1]), list)
